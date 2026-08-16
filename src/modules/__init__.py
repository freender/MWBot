import logging
import threading
import time

import cfg
from telebot.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from modules.common import build_api_headers, normalize_base_url, request_json
from modules.firewall import (
    disable_asn_to_firewall_rule,
    get_firewall_status_text,
    get_next_firewall_run,
    get_asns_from_firewall_rule,
    get_rule_status,
    grant_network_access,
    schedule_fw_task,
)
from modules.network_check import (
    create_network_check,
    delete_network_check,
    get_network_check,
    network_check_is_configured,
)
from modules.incidents import (
    build_incident_body,
    build_incident_title,
    create_incident,
    find_triage_reports,
    get_open_incident_index,
    incident_creation_is_configured,
)
from modules.maintenance import (
    clear_alertmanager_mw_state,
    format_duration,
    format_remaining,
    get_alertmanager_window_text,
    load_alertmanager_mw_state,
    start_alertmanager_mw,
    stop_alertmanager_mw,
)
from modules.redownload import (
    build_issue_label,
    build_redownload_confirmation,
    clear_seerr_read_caches,
    execute_redownload,
    find_seerr_issue_for_media,
    get_all_seerr_issue_ids,
    get_issue_target,
    get_open_seerr_issues,
    get_seerr_issue,
    get_seerr_media_details,
    is_issue_open,
    parse_seerr_issue_url,
    parse_seerr_reference,
    resolve_redownload_issue,
    resolve_seerr_issue,
    select_failed_history_record,
)


DEFAULT_COMMANDS = {
    'start': 'Open main menu',
}

AUTH_COMMANDS = dict(DEFAULT_COMMANDS)

OWNER_COMMANDS = {
    **DEFAULT_COMMANDS,
    'incident': 'Create a homelab incident',
}

COMMANDS = OWNER_COMMANDS

SEERR_OWNER_USER_ID = 1

_seerr_access_cache = {
    'authorized_chat_ids': set(),
    'owner_chat_ids': set(),
    'loaded': False,
    'refreshed_at': None,
}
_SEERR_ACCESS_CACHE_LOCK = threading.Lock()
SEERR_ACCESS_CACHE_TTL = 60


def is_command(string):
    if not string:
        return False
    return string.startswith('/')


def _coerce_chat_id(value):
    if value in (None, ''):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _get_seerr_users():
    users = []
    skip = 0
    take = 100
    base_url = normalize_base_url(cfg.SEERR_BASE_URL)

    while True:
        payload = request_json(
            'GET',
            f'{base_url}/api/v1/user',
            headers=build_api_headers(cfg.SEERR_API_KEY),
            params={'take': take, 'skip': skip},
        ) or {}
        results = payload.get('results') or []
        users.extend(results)
        if len(results) < take:
            return users
        skip += take


def _get_seerr_notification_settings(user_id):
    base_url = normalize_base_url(cfg.SEERR_BASE_URL)
    return request_json(
        'GET',
        f'{base_url}/api/v1/user/{user_id}/settings/notifications',
        headers=build_api_headers(cfg.SEERR_API_KEY),
    ) or {}


def _refresh_seerr_access_cache():
    authorized_chat_ids = set()
    owner_chat_ids = set()

    users = _get_seerr_users()
    for user in users:
        user_id = user.get('id')
        if user_id is None:
            continue

        settings = _get_seerr_notification_settings(user_id)
        telegram_chat_id = _coerce_chat_id(settings.get('telegramChatId'))
        if telegram_chat_id is None:
            continue

        authorized_chat_ids.add(telegram_chat_id)
        if user_id == SEERR_OWNER_USER_ID:
            owner_chat_ids.add(telegram_chat_id)

    _seerr_access_cache.update({
        'authorized_chat_ids': authorized_chat_ids,
        'owner_chat_ids': owner_chat_ids,
        'loaded': True,
    })


def get_seerr_access_cache():
    return _seerr_access_cache


def _apply_access_test_override():
    test_user_id = cfg.SEERR_ACCESS_TEST_USER_ID
    test_mode = cfg.SEERR_ACCESS_TEST_MODE
    if test_user_id is None or test_mode in ('', 'normal'):
        return

    authorized_chat_ids = set(_seerr_access_cache.get('authorized_chat_ids') or set())
    owner_chat_ids = set(_seerr_access_cache.get('owner_chat_ids') or set())

    owner_chat_ids.discard(test_user_id)

    if test_mode == 'owner':
        authorized_chat_ids.add(test_user_id)
        owner_chat_ids.add(test_user_id)
    elif test_mode == 'authorized':
        authorized_chat_ids.add(test_user_id)
    elif test_mode == 'unauthorized':
        authorized_chat_ids.discard(test_user_id)
    else:
        logging.warning('Ignoring invalid SEERR_ACCESS_TEST_MODE value: %s', test_mode)
        return

    _seerr_access_cache.update({
        'authorized_chat_ids': authorized_chat_ids,
        'owner_chat_ids': owner_chat_ids,
        'loaded': True,
    })
    logging.info('Applied Seerr access test override for Telegram ID %s: %s', test_user_id, test_mode)


def warm_seerr_access_cache():
    try:
        _refresh_seerr_access_cache()
    except Exception as exc:
        logging.warning('Unable to load Seerr Telegram access cache on startup: %s', exc)
        _seerr_access_cache.update({
            'authorized_chat_ids': set(),
            'owner_chat_ids': set(),
            'loaded': True,
        })

    _apply_access_test_override()
    _seerr_access_cache['refreshed_at'] = time.monotonic()
    cache = get_seerr_access_cache()
    logging.info(
        'Seerr Telegram access cache ready: %s authorized, %s owners',
        len(cache['authorized_chat_ids']),
        len(cache['owner_chat_ids']),
    )
    return cache


def _refresh_seerr_access_cache_if_stale():
    refreshed_at = _seerr_access_cache.get('refreshed_at')
    if refreshed_at is None or time.monotonic() - refreshed_at < SEERR_ACCESS_CACHE_TTL:
        return
    with _SEERR_ACCESS_CACHE_LOCK:
        refreshed_at = _seerr_access_cache.get('refreshed_at')
        if refreshed_at is None or time.monotonic() - refreshed_at >= SEERR_ACCESS_CACHE_TTL:
            warm_seerr_access_cache()


def _build_bot_commands(command_map):
    return [BotCommand(name, description) for name, description in command_map.items()]


def register_bot_commands(bot, access_cache=None):
    cache = access_cache or get_seerr_access_cache()
    owner_chat_ids = set(cache.get('owner_chat_ids') or set())
    authorized_chat_ids = set(cache.get('authorized_chat_ids') or set())

    bot.set_my_commands(
        _build_bot_commands(DEFAULT_COMMANDS),
        scope=BotCommandScopeDefault(),
    )

    for chat_id in sorted(authorized_chat_ids - owner_chat_ids):
        bot.set_my_commands(
            _build_bot_commands(AUTH_COMMANDS),
            scope=BotCommandScopeChat(chat_id),
        )

    for chat_id in sorted(owner_chat_ids):
        bot.set_my_commands(
            _build_bot_commands(OWNER_COMMANDS),
            scope=BotCommandScopeChat(chat_id),
        )


def is_owner_chat_id(chat_id):
    _refresh_seerr_access_cache_if_stale()
    return bool(chat_id in get_seerr_access_cache()['owner_chat_ids'])


def get_owner_chat_ids():
    """The owner's own Telegram chat(s) with the bot -- never a shared group.

    Backed by the same Seerr-derived identity that gates owner-only commands like
    /incident, so anything that should reach the owner personally (and not a group where
    other people are watching) targets this instead of cfg.CHAT_ID.
    """
    _refresh_seerr_access_cache_if_stale()
    return set(get_seerr_access_cache()['owner_chat_ids'])


def is_auth_chat_id(chat_id):
    _refresh_seerr_access_cache_if_stale()
    return bool(chat_id in get_seerr_access_cache()['authorized_chat_ids'])
