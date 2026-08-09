import telebot
import cfg
import logging
import signal
import threading
import time
from html import escape
from urllib.parse import urlparse, urlunparse
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from modules import (
    build_issue_label,
    build_redownload_confirmation,
    clear_seerr_read_caches,
    create_incident,
    create_network_check,
    delete_network_check,
    disable_asn_to_firewall_rule,
    execute_redownload,
    format_duration,
    get_firewall_status_text,
    get_alertmanager_mw_status_text,
    get_network_check,
    get_open_seerr_issues,
    grant_network_access,
    is_auth_chat_id,
    is_command,
    is_owner_chat_id,
    incident_creation_is_configured,
    network_check_is_configured,
    register_bot_commands,
    resolve_redownload_issue,
    schedule_fw_task,
    start_alertmanager_mw,
    stop_alertmanager_mw,
    warm_seerr_access_cache,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

bot = telebot.TeleBot(cfg.TOKEN)
shutdown_event = threading.Event()

NETWORK_CHECK_POLL_INTERVAL = 1
NETWORK_CHECK_TIMEOUT = 5 * 60

# -- Pending redownload targets keyed by "chat_id:user_id" --
_pending_redownloads = {}

# Short-lived Worker sessions keyed by the authorized Telegram chat and user.
_pending_network_checks = {}

# -- Latest menu message keyed by chat_id -- keeps /start tidy by replacing old menu messages
_home_menu_messages = {}


def start_background_threads(active_bot):
    scheduler_thread = threading.Thread(target=schedule_fw_task, args=(shutdown_event,), daemon=True)
    scheduler_thread.start()



def handle_shutdown(signum, _frame):
    logging.info('Received signal %s; stopping bot', signum)
    shutdown_event.set()
    stop_polling = getattr(bot, 'stop_polling', None)
    if stop_polling is not None:
        stop_polling()


def _get_user_id(message):
    return getattr(getattr(message, 'from_user', None), 'id', None)


def _pending_key(chat_id, user_id):
    return f'{chat_id}:{user_id}'


def _get_seerr_browser_url():
    configured_url = (cfg.SEERR_PUBLIC_URL or cfg.SEERR_BASE_URL).strip().rstrip('/')
    if not configured_url:
        return None

    base_url = configured_url
    parsed = urlparse(base_url)

    if parsed.netloc:
        hostname = parsed.hostname or ''
        if '.' not in hostname or hostname in ('localhost', '127.0.0.1'):
            return None
        return urlunparse(('https', parsed.netloc, parsed.path.rstrip('/'), '', '', ''))

    hostname = base_url.lstrip('/').split('/', 1)[0].split(':', 1)[0]
    if '.' not in hostname or hostname in ('localhost', '127.0.0.1'):
        return None
    return f"https://{base_url.lstrip('/')}"


# ── Inline Keyboard Helpers ──────────────────────────────────────────

def _cancel_markup(cancel_callback='cancel', cancel_label='Cancel'):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(cancel_label, callback_data=cancel_callback))
    return markup


def _confirm_cancel_markup(confirm_data, confirm_label='Confirm', cancel_callback='cancel', cancel_label='Cancel'):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton(confirm_label, callback_data=confirm_data),
        InlineKeyboardButton(cancel_label, callback_data=cancel_callback),
    )
    return markup


def _show_menu(chat_id, text, reply_markup, message_id=None):
    if message_id is not None:
        try:
            bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode='HTML',
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            return message_id
        except Exception as exc:
            if 'message is not modified' in str(exc).lower():
                return message_id
            logging.warning('Unable to update menu message %s in chat %s: %s', message_id, chat_id, exc)

    sent = bot.send_message(
        chat_id,
        text,
        parse_mode='HTML',
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )
    return getattr(sent, 'message_id', None)


def _delete_bot_message(chat_id, message_id):
    if not chat_id or not message_id:
        return False
    try:
        bot.delete_message(chat_id, message_id)
        return True
    except Exception as exc:
        logging.warning('Unable to delete message %s in chat %s: %s', message_id, chat_id, exc)
        return False


def _clear_home_menu_message(chat_id, message_id):
    if _home_menu_messages.get(chat_id) == message_id:
        _home_menu_messages.pop(chat_id, None)


def _home_markup(user_id):
    markup = InlineKeyboardMarkup(row_width=2)
    if is_auth_chat_id(user_id):
        markup.add(
            InlineKeyboardButton('📡 Plex Access', callback_data='nav_plex'),
            InlineKeyboardButton('🎬 Media', callback_data='nav_media'),
        )
    if is_owner_chat_id(user_id):
        markup.add(
            InlineKeyboardButton('🔕 Alertmanager MW', callback_data='nav_am_mw'),
            InlineKeyboardButton('🚨 New Incident', callback_data='incident_new'),
        )
    markup.add(InlineKeyboardButton('✖ Close', callback_data='menu_close'))
    return markup


def _plex_markup(user_id):
    markup = InlineKeyboardMarkup(row_width=2)
    row = [InlineKeyboardButton('✅ Allow Plex', callback_data='plex_allow')]
    if is_owner_chat_id(user_id):
        row.append(InlineKeyboardButton('🧹 Remove Access', callback_data='plex_reset'))
    markup.add(*row)
    markup.add(InlineKeyboardButton('📋 Status', callback_data='plex_status'))
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_home'))
    return markup


def _media_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton('📋 Pick Open Issue', callback_data='media_redownload'))
    seerr_browser_url = _get_seerr_browser_url()
    if seerr_browser_url:
        markup.add(InlineKeyboardButton('🌐 Open Seerr', url=seerr_browser_url))
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_home'))
    return markup


def _alertmanager_mw_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('▶ Start', callback_data='am_mw_start'),
        InlineKeyboardButton('⏹ Stop', callback_data='am_mw_stop'),
    )
    markup.add(
        InlineKeyboardButton('📋 Status', callback_data='am_mw_status'),
        InlineKeyboardButton('⬅ Back', callback_data='nav_home'),
    )
    return markup


def _plex_result_markup(user_id):
    markup = InlineKeyboardMarkup(row_width=2)
    row = [InlineKeyboardButton('✅ Allow Plex', callback_data='plex_allow')]
    if is_owner_chat_id(user_id):
        row.append(InlineKeyboardButton('🧹 Remove Access', callback_data='plex_reset'))
    markup.add(*row)
    markup.add(InlineKeyboardButton('📋 Status', callback_data='plex_status'))
    markup.add(
        InlineKeyboardButton('⬅ Back', callback_data='nav_plex'),
        InlineKeyboardButton('🏠 Home', callback_data='nav_home'),
    )
    return markup


def _network_check_markup(check_url):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton('🌐 Detect Current Network', url=check_url))
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_plex'))
    return markup


def _media_result_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton('📋 Pick Open Issue', callback_data='media_redownload'))
    seerr_browser_url = _get_seerr_browser_url()
    if seerr_browser_url:
        markup.add(InlineKeyboardButton('🌐 Open Seerr', url=seerr_browser_url))
    markup.add(
        InlineKeyboardButton('⬅ Back', callback_data='nav_media'),
        InlineKeyboardButton('🏠 Home', callback_data='nav_home'),
    )
    return markup


def _alertmanager_mw_result_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('📋 Status', callback_data='am_mw_status'),
        InlineKeyboardButton('⬅ Back', callback_data='nav_am_mw'),
    )
    markup.add(InlineKeyboardButton('🏠 Home', callback_data='nav_home'))
    return markup


def _incident_result_markup(incident=None):
    markup = InlineKeyboardMarkup(row_width=1)
    if incident:
        markup.add(InlineKeyboardButton(f'Open Incident #{incident["number"]}', url=incident['url']))
    markup.add(InlineKeyboardButton('🏠 Home', callback_data='nav_home'))
    return markup


def _show_home_menu(chat_id, user_id=None, message_id=None):
    display_user_id = user_id if user_id is not None else chat_id
    has_auth_access = is_auth_chat_id(display_user_id)
    has_owner_access = is_owner_chat_id(display_user_id)
    if has_auth_access or has_owner_access:
        id_line = f'<b>Your Telegram ID:</b> <code>{display_user_id}</code>\n\n'
        body = '<b>Choose a section</b> to manage Plex, redownloads, or maintenance windows.'
    else:
        id_line = (
            f'<b>Your Telegram ID:</b> <code>{display_user_id}</code>\n'
            'Paste this into Seerr -> Notifications -> Telegram Chat ID.\n\n'
        )
        body = 'Once your Telegram ID is added in Seerr, reopen /start to see available actions.'
    text = (
        '🤖 <b>MWBot</b>\n\n'
        + id_line
        + body
    )
    menu_message_id = _show_menu(
        chat_id,
        text,
        _home_markup(display_user_id),
        message_id=message_id,
    )
    if menu_message_id is not None:
        _home_menu_messages[chat_id] = menu_message_id


def _show_plex_menu(chat_id, user_id=None, message_id=None):
    display_user_id = user_id if user_id is not None else chat_id
    _show_menu(
        chat_id,
        '📡 <b>Plex Access</b>\n'
        'Allow Plex from your current location or remove the temporary rule when you are done.',
        _plex_markup(display_user_id),
        message_id=message_id,
    )


def _show_media_menu(chat_id, message_id=None):
    _show_menu(
        chat_id,
        '🎬 <b>Media</b>\n'
        'Pick an open Seerr issue to replace a bad release, or jump into Seerr first.',
        _media_markup(),
        message_id=message_id,
    )


def _show_alertmanager_mw_menu(chat_id, message_id=None):
    _show_menu(
        chat_id,
        '🔕 <b>Alertmanager Maintenance</b>\n'
        'Silence Alertmanager notifications during maintenance.\n\n'
        f'- Start uses a {format_duration(cfg.ALERTMANAGER_OPEN_MW_DURATION)} safety expiry\n'
        '- Status shows API health, active alerts, and maintenance state\n'
        '- Stop completes the active Alertmanager window',
        _alertmanager_mw_markup(),
        message_id=message_id,
    )


def _show_plex_result(chat_id, text, user_id=None, message_id=None):
    display_user_id = user_id if user_id is not None else chat_id
    _show_menu(
        chat_id,
        '📡 <b>Plex Access</b>\n' + escape(text),
        _plex_result_markup(display_user_id),
        message_id=message_id,
    )


def _show_media_result(chat_id, text, message_id=None):
    _show_menu(
        chat_id,
        '🎬 <b>Redownload</b>\n' + escape(text),
        _media_result_markup(),
        message_id=message_id,
    )


def _show_alertmanager_mw_result(chat_id, text, message_id=None):
    _show_menu(
        chat_id,
        '🔕 <b>Alertmanager Maintenance</b>\n' + escape(text),
        _alertmanager_mw_result_markup(),
        message_id=message_id,
    )


def _show_incident_result(chat_id, text, incident=None, message_id=None):
    _show_menu(
        chat_id,
        '🚨 <b>Homelab Incident</b>\n' + escape(text),
        _incident_result_markup(incident=incident),
        message_id=message_id,
    )


# ── Menu entry points ────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def command_start(message):
    previous_menu_message_id = _home_menu_messages.pop(message.chat.id, None)
    if previous_menu_message_id is not None:
        _delete_bot_message(message.chat.id, previous_menu_message_id)
    _show_home_menu(message.chat.id, user_id=_get_user_id(message))


def _message_text(message):
    return (getattr(message, 'text', None) or getattr(message, 'caption', None) or '').strip()


def _create_incident_from_telegram(chat_id, summary, source_text=None, message_id=None):
    bot.send_chat_action(chat_id, 'typing')
    incident, error = create_incident(summary, source_text=source_text)
    if incident is None:
        _show_incident_result(chat_id, error or 'Unable to create the incident.', message_id=message_id)
        return
    _show_incident_result(
        chat_id,
        f'Incident #{incident["number"]} created. OpenCode triage is queued.',
        incident=incident,
        message_id=message_id,
    )


def _handle_incident_description(message, expected_user_id, menu_message_id):
    if _get_user_id(message) != expected_user_id or not is_owner_chat_id(expected_user_id):
        _answer_not_allowed(message.chat.id)
        return
    summary = _message_text(message)
    if not summary or is_command(summary):
        _show_incident_result(
            message.chat.id,
            'Incident creation cancelled. Use /incident followed by a description.',
            message_id=menu_message_id,
        )
        return
    _create_incident_from_telegram(message.chat.id, summary, message_id=menu_message_id)


def _start_incident_flow(chat_id, user_id, message_id=None):
    if not incident_creation_is_configured():
        _show_incident_result(chat_id, 'GitHub incident creation is not configured.', message_id=message_id)
        return
    prompt_message_id = _show_menu(
        chat_id,
        '🚨 <b>New Incident</b>\nSend one message describing the symptom and affected host or service.',
        _cancel_markup(cancel_callback='nav_home', cancel_label='⬅ Back'),
        message_id=message_id,
    ) or message_id
    bot.register_next_step_handler_by_chat_id(
        chat_id,
        _handle_incident_description,
        user_id,
        prompt_message_id,
    )


@bot.message_handler(commands=['incident'])
def command_incident(message):
    user_id = _get_user_id(message)
    if not is_owner_chat_id(user_id):
        _answer_not_allowed(message.chat.id)
        return

    command_text = _message_text(message)
    summary = command_text.partition(' ')[2].strip()
    replied_message = getattr(message, 'reply_to_message', None)
    replied_text = _message_text(replied_message) if replied_message else ''
    if not summary and replied_text:
        summary = replied_text
    if not summary:
        _start_incident_flow(message.chat.id, user_id)
        return
    _create_incident_from_telegram(
        message.chat.id,
        summary,
        source_text=replied_text or None,
    )


def _start_redownload_flow(chat_id, user_id, message_id=None):
    bot.send_chat_action(chat_id, 'typing')
    seerr_browser_url = _get_seerr_browser_url()
    try:
        open_issues = get_open_seerr_issues()
    except Exception as exc:
        logging.error('Failed to fetch open Seerr issues: %s', exc, exc_info=True)
        open_issues = []

    if open_issues:
        markup = InlineKeyboardMarkup(row_width=1)
        for issue in open_issues:
            issue_id = issue.get('id')
            label = build_issue_label(issue)
            markup.add(InlineKeyboardButton(label, callback_data=f'redownload_issue:{issue_id}'))
        if seerr_browser_url:
            markup.add(InlineKeyboardButton('Open Seerr', url=seerr_browser_url))
        if message_id is not None:
            markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_media'))
            _show_menu(
                chat_id,
                '🎬 <b>Pick Open Issue</b>\n'
                'Choose the title with the bad release.\n'
                'If it is not listed, open Seerr first and create a new issue.',
                markup,
                message_id=message_id,
            )
        else:
            markup.add(InlineKeyboardButton('Cancel', callback_data='cancel'))
            bot.send_message(
                chat_id,
                'Pick the title with the bad release.\nIf it is not listed, create a new issue in Seerr first.',
                reply_markup=markup,
            )
    else:
        markup = InlineKeyboardMarkup(row_width=1)
        if seerr_browser_url:
            markup.add(InlineKeyboardButton('Open Seerr', url=seerr_browser_url))
        if message_id is not None:
            markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_media'))
            _show_menu(
                chat_id,
                '🎬 <b>No Open Issues</b>\n'
                'Create a new issue in Seerr, then come back here.',
                markup,
                message_id=message_id,
            )
        else:
            markup.add(InlineKeyboardButton('Cancel', callback_data='cancel'))
            bot.send_message(
                chat_id,
                'No open redownload issues right now.\nCreate a new issue in Seerr, then come back here.',
                reply_markup=markup,
            )


def _start_network_check(chat_id, user_id, message_id=None):
    if not network_check_is_configured():
        _show_plex_result(
            chat_id,
            '❌ Access Not Enabled\nAutomatic network detection is not configured.',
            user_id=user_id,
            message_id=message_id,
        )
        return

    bot.send_chat_action(chat_id, 'typing')
    session, error = create_network_check()
    if session is None:
        _show_plex_result(
            chat_id,
            '❌ Access Not Enabled\n' + (error or 'Automatic network detection is unavailable.'),
            user_id=user_id,
            message_id=message_id,
        )
        return

    key = _pending_key(chat_id, user_id)
    pending = {
        **session,
        'chat_id': chat_id,
        'user_id': user_id,
        'message_id': message_id,
    }
    _pending_network_checks[key] = pending
    pending['message_id'] = _show_menu(
        chat_id,
        '📡 <b>Allow Plex</b>\n'
        '⏳ <b>Waiting for detection</b>\n\n'
        'Open the link on the network you want to use.\n'
        'Expires in 5 minutes.',
        _network_check_markup(session['check_url']),
        message_id=message_id,
    ) or message_id
    threading.Thread(target=_poll_network_check, args=(key, pending), daemon=True).start()


def _finish_network_check(key, pending, result, success):
    if _pending_network_checks.get(key) is not pending:
        return
    _pending_network_checks.pop(key, None)
    heading = '✅ Access Enabled' if success else '❌ Access Not Enabled'
    _show_plex_result(
        pending['chat_id'],
        f'{heading}\n{result}',
        user_id=pending['user_id'],
        message_id=pending['message_id'],
    )


def _poll_network_check(key, pending):
    deadline = time.monotonic() + NETWORK_CHECK_TIMEOUT
    while time.monotonic() < deadline and not shutdown_event.wait(NETWORK_CHECK_POLL_INTERVAL):
        if _pending_network_checks.get(key) is not pending:
            return

        detected, error = get_network_check(pending['id'])
        if detected is None:
            if error and 'expired' in error.lower():
                _finish_network_check(
                    key,
                    pending,
                    'Network check expired. Select Allow Plex and try again.',
                    success=False,
                )
                return
            logging.warning('Unable to poll network detection session; retrying')
            continue
        if detected['status'] == 'pending':
            continue
        if _pending_network_checks.get(key) is not pending:
            return
        if not is_auth_chat_id(pending['user_id']):
            if not delete_network_check(pending['id']):
                logging.warning('Unable to consume revoked network detection session')
            _finish_network_check(
                key,
                pending,
                'Access permission is no longer available. Select Allow Plex and try again.',
                success=False,
            )
            return

        success, result = grant_network_access(
            detected['asn'],
            detected.get('as_organization'),
        )
        if success:
            if not delete_network_check(pending['id']):
                logging.warning('Unable to consume completed network detection session')
            _finish_network_check(
                key,
                pending,
                'Your current network now has Plex access.',
                success=True,
            )
            return

        _finish_network_check(
            key,
            pending,
            result or 'Unable to update the firewall rule.',
            success=False,
        )
        return

    _finish_network_check(
        key,
        pending,
        'Network check expired. Select Allow Plex and try again.',
        success=False,
    )


# ── Callback Queries (inline button presses) ────────────────────────

def _answer_not_allowed(chat_id):
    bot.send_message(chat_id, 'Sorry you are not allowed to use this command!')


def _require_auth_callback(call):
    bot.answer_callback_query(call.id)
    if not is_auth_chat_id(call.from_user.id):
        _answer_not_allowed(call.message.chat.id)
        return False
    return True


def _require_owner_callback(call):
    bot.answer_callback_query(call.id)
    if not is_owner_chat_id(call.from_user.id):
        _answer_not_allowed(call.message.chat.id)
        return False
    return True


def _handle_cancel(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    key = _pending_key(chat_id, user_id)
    _pending_redownloads.pop(key, None)
    _pending_network_checks.pop(key, None)
    bot.edit_message_reply_markup(
        chat_id=chat_id,
        message_id=call.message.message_id,
        reply_markup=None,
    )
    bot.answer_callback_query(call.id, text='Cancelled')


def _handle_menu_close(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    bot.answer_callback_query(call.id)
    _clear_home_menu_message(chat_id, message_id)
    if _delete_bot_message(chat_id, message_id):
        return
    bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)


def _handle_nav_home(call):
    bot.answer_callback_query(call.id)
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    _show_home_menu(call.message.chat.id, user_id=call.from_user.id, message_id=call.message.message_id)


def _handle_nav_plex(call):
    if not _require_auth_callback(call):
        return
    _pending_network_checks.pop(_pending_key(call.message.chat.id, call.from_user.id), None)
    _show_plex_menu(call.message.chat.id, user_id=call.from_user.id, message_id=call.message.message_id)


def _handle_nav_media(call):
    if not _require_auth_callback(call):
        return
    _show_media_menu(call.message.chat.id, message_id=call.message.message_id)


def _handle_nav_alertmanager_mw(call):
    if not _require_owner_callback(call):
        return
    _show_alertmanager_mw_menu(call.message.chat.id, message_id=call.message.message_id)


def _handle_plex_allow(call):
    if not _require_auth_callback(call):
        return
    _start_network_check(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


def _handle_plex_allow_manual(call):
    if not _require_auth_callback(call):
        return
    _show_plex_result(
        call.message.chat.id,
        'Manual IP entry has been retired. Select Allow Plex to detect the network automatically.',
        user_id=call.from_user.id,
        message_id=call.message.message_id,
    )


def _handle_plex_detect_apply(call):
    if not _require_auth_callback(call):
        return
    _show_plex_result(
        call.message.chat.id,
        'Detection is now automatic. Select Allow Plex to start a new network check.',
        user_id=call.from_user.id,
        message_id=call.message.message_id,
    )


def _handle_plex_reset(call):
    if not _require_owner_callback(call):
        return
    chat_id = call.message.chat.id
    bot.send_chat_action(chat_id, 'typing')
    result = disable_asn_to_firewall_rule()
    _show_plex_result(
        chat_id,
        result or 'Unable to update firewall rule.',
        user_id=call.from_user.id,
        message_id=call.message.message_id,
    )


def _handle_plex_status(call):
    if not _require_auth_callback(call):
        return
    chat_id = call.message.chat.id
    bot.send_chat_action(chat_id, 'typing')
    _show_plex_result(
        chat_id,
        get_firewall_status_text(),
        user_id=call.from_user.id,
        message_id=call.message.message_id,
    )


def _handle_media_redownload(call):
    if not _require_auth_callback(call):
        return
    _start_redownload_flow(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


def _handle_alertmanager_mw_action(call, result):
    _show_alertmanager_mw_result(call.message.chat.id, result, message_id=call.message.message_id)


def _handle_alertmanager_mw_start(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_alertmanager_mw_action(call, start_alertmanager_mw())


def _handle_alertmanager_mw_stop(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_alertmanager_mw_action(call, stop_alertmanager_mw())


def _handle_alertmanager_mw_status(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_alertmanager_mw_action(call, get_alertmanager_mw_status_text())


def _handle_incident_new(call):
    if not _require_owner_callback(call):
        return
    _start_incident_flow(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


CALLBACK_HANDLERS = {
    'cancel': _handle_cancel,
    'menu_close': _handle_menu_close,
    'nav_home': _handle_nav_home,
    'nav_plex': _handle_nav_plex,
    'nav_media': _handle_nav_media,
    'nav_am_mw': _handle_nav_alertmanager_mw,
    'plex_allow': _handle_plex_allow,
    'cmd_ip': _handle_plex_allow,
    'plex_allow_manual': _handle_plex_allow_manual,
    'plex_detect_apply': _handle_plex_detect_apply,
    'plex_reset': _handle_plex_reset,
    'plex_status': _handle_plex_status,
    'media_redownload': _handle_media_redownload,
    'cmd_redownload': _handle_media_redownload,
    'am_mw_start': _handle_alertmanager_mw_start,
    'am_mw_stop': _handle_alertmanager_mw_stop,
    'am_mw_status': _handle_alertmanager_mw_status,
    'incident_new': _handle_incident_new,
}


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    handler = CALLBACK_HANDLERS.get(data)
    if handler is not None:
        handler(call)
        return

    # Redownload: user picked an issue from the list
    if data.startswith('redownload_issue:'):
        if not is_auth_chat_id(user_id):
            bot.answer_callback_query(call.id, text='Not authorized')
            return
        issue_id_str = data.split(':', 1)[1]
        bot.answer_callback_query(call.id)
        bot.send_chat_action(chat_id, 'typing')
        seerr_url = f'{cfg.SEERR_BASE_URL}/issues/{issue_id_str}'
        target, error = resolve_redownload_issue(seerr_url)
        if target is None:
            _show_menu(
                chat_id,
                f'🎬 <b>Pick Open Issue</b>\n{error or "Unable to resolve Seerr issue."}',
                _cancel_markup(cancel_callback='media_redownload', cancel_label='⬅ Back'),
                message_id=call.message.message_id,
            )
            return
        key = _pending_key(chat_id, user_id)
        _pending_redownloads[key] = target
        confirm_label = 'Continue Anyway' if target.get('original_language_name') and target.get('original_language_name') != 'English' else 'Confirm'
        _show_menu(
            chat_id,
            build_redownload_confirmation(target),
            _confirm_cancel_markup(
                'redownload_confirm',
                confirm_label=confirm_label,
                cancel_callback='media_redownload',
                cancel_label='⬅ Back',
            ),
            message_id=call.message.message_id,
        )
        return

    # Redownload confirm
    if data == 'redownload_confirm':
        key = _pending_key(chat_id, user_id)
        target = _pending_redownloads.pop(key, None)
        if not is_auth_chat_id(user_id):
            bot.answer_callback_query(call.id, text='Not authorized')
            _answer_not_allowed(chat_id)
            return
        if target is None:
            bot.answer_callback_query(call.id, text='Session expired. Open Media from /start and try again.')
            return
        bot.answer_callback_query(call.id, text='Processing...')
        _show_menu(
            chat_id,
            build_redownload_confirmation(target) + '\n\n⏳ Processing...',
            None,
            message_id=call.message.message_id,
        )
        bot.send_chat_action(chat_id, 'typing')
        result = execute_redownload(target)
        clear_seerr_read_caches()
        _show_media_result(chat_id, result or 'Redownload request completed.', message_id=call.message.message_id)
        return

    bot.answer_callback_query(call.id)


# ── Unknown command handler ──────────────────────────────────────────

@bot.message_handler(func=lambda message: is_command(message.text))
def command_unknown(message):
    command = str(message.text).split()[0]
    bot.reply_to(
        message,
        'Sorry, {} is not available.\nUse /start to open the menu.'.format(command),
    )


def main():
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    warm_seerr_access_cache()
    register_bot_commands(bot)
    start_background_threads(bot)
    bot.infinity_polling()


if __name__ == '__main__':
    main()
