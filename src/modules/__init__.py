import logging

import cfg
from telebot.types import BotCommand
from modules.common import build_api_headers, normalize_base_url, request_json
from modules.firewall import (
    add_asn_to_firewall_rule,
    convert_to_local_time,
    disable_asn_to_firewall_rule,
    get_asn_from_ip,
    get_asns_from_firewall_rule,
    get_next_firewall_run,
    get_rule_modify_date,
    get_rule_status,
    is_valid_ip,
    schedule_fw_task,
)
from modules.maintenance import (
    build_mw_state,
    clear_mw_state,
    delete_message,
    format_duration,
    get_mw_status_text,
    load_mw_state,
    maintain_timed_mw,
    parse_duration,
    replace_mw_state,
    save_mw_state,
    start_mw,
    stop_mw,
    stop_timed_mw,
)
from modules.redownload import (
    ISSUE_STATUS_OPEN,
    ISSUE_STATUS_RESOLVED,
    build_issue_label,
    build_redownload_confirmation,
    build_target_label,
    delete_queue_item,
    execute_redownload,
    find_queue_item,
    find_seerr_issue_for_media,
    get_all_seerr_issue_ids,
    get_episode,
    get_issue_target,
    get_open_seerr_issues,
    get_seerr_issue,
    get_seerr_media_details,
    is_issue_open,
    issue_sort_key,
    mark_history_failed,
    parse_seerr_issue_url,
    parse_seerr_reference,
    process_radarr_redownload,
    process_sonarr_redownload,
    resolve_redownload_issue,
    resolve_seerr_issue,
    select_failed_history_record,
)


COMMANDS = {
    'start': 'Open main menu',
    'ip': 'Allow Plex in unknown place',
    'reset_ip': 'Disable Plex in unknown place',
    'redownload': 'Blacklist issue release and prevent re-download',
    'mw': 'Open maintenance quick actions',
    'help': 'Show help',
}

SEERR_OWNER_USER_ID = 1

_seerr_access_cache = {
    'authorized_chat_ids': set(),
    'owner_chat_ids': set(),
    'loaded': False,
}


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


def _get_env_authorized_chat_ids():
    return {
        chat_id for chat_id in (_coerce_chat_id(value) for value in cfg.TELEGRAM_AUTH_USERS)
        if chat_id is not None
    }


def _get_env_owner_chat_ids():
    owner_chat_id = _coerce_chat_id(cfg.OWNER)
    if owner_chat_id is None:
        return set()
    return {owner_chat_id}


def _get_message_telegram_id(message):
    from_user = getattr(message, 'from_user', None)
    from_user_id = getattr(from_user, 'id', None)
    if from_user_id is not None:
        return from_user_id

    chat = getattr(message, 'chat', None)
    return getattr(chat, 'id', None)


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
    authorized_chat_ids = set(_get_env_authorized_chat_ids())
    owner_chat_ids = set(_get_env_owner_chat_ids())

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


def warm_seerr_access_cache():
    try:
        _refresh_seerr_access_cache()
    except Exception as exc:
        logging.warning('Unable to load Seerr Telegram access cache on startup: %s', exc)
        _seerr_access_cache.update({
            'authorized_chat_ids': set(_get_env_authorized_chat_ids()),
            'owner_chat_ids': set(_get_env_owner_chat_ids()),
            'loaded': True,
        })

    cache = get_seerr_access_cache()
    logging.info(
        'Seerr Telegram access cache ready: %s authorized, %s owners',
        len(cache['authorized_chat_ids']),
        len(cache['owner_chat_ids']),
    )
    return cache


def register_bot_commands(bot):
    bot.set_my_commands([
        BotCommand(name, description)
        for name, description in COMMANDS.items()
    ])


def is_owner_chat_id(chat_id):
    return bool(chat_id in get_seerr_access_cache()['owner_chat_ids'])


def is_auth_chat_id(chat_id):
    return bool(chat_id in get_seerr_access_cache()['authorized_chat_ids'])


def is_owner(message):
    telegram_id = _get_message_telegram_id(message)
    logging.warning('Owner auth attempt for Telegram ID: %s', telegram_id)
    return is_owner_chat_id(telegram_id)


def is_auth_user(message):
    telegram_id = _get_message_telegram_id(message)
    logging.warning('User auth attempt for Telegram ID: %s', telegram_id)
    return is_auth_chat_id(telegram_id)
