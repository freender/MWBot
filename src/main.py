import telebot
import cfg
import logging
import threading
from datetime import timedelta
from functools import wraps
from html import escape
from urllib.parse import urlparse, urlunparse
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from modules import (
    add_asn_to_firewall_rule,
    build_issue_label,
    build_mw_state,
    build_redownload_confirmation,
    disable_asn_to_firewall_rule,
    execute_redownload,
    format_duration,
    get_asn_from_ip,
    get_mw_status_text,
    get_open_seerr_issues,
    is_auth_chat_id,
    is_auth_user,
    is_command,
    is_owner_chat_id,
    is_owner,
    is_valid_ip,
    maintain_timed_mw,
    replace_mw_state,
    register_bot_commands,
    resolve_redownload_issue,
    schedule_fw_task,
    start_mw,
    stop_timed_mw,
    warm_seerr_access_cache,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

bot = telebot.TeleBot(cfg.TOKEN)

DEFAULT_REBOOT_MW_DURATION = timedelta(minutes=5)
DEFAULT_FIRMWARE_MW_DURATION = timedelta(minutes=5)

# -- Pending redownload targets keyed by "chat_id:user_id" --
_pending_redownloads = {}

# -- Active next-step flows keyed by chat_id -- tracks whether a flow is active
# so Cancel can invalidate it and next-step handlers can check before running
_active_flows = {}


def start_background_threads(active_bot):
    scheduler_thread = threading.Thread(target=schedule_fw_task, daemon=True)
    scheduler_thread.start()

    timed_mw_thread = threading.Thread(target=maintain_timed_mw, args=(active_bot,), daemon=True)
    timed_mw_thread.start()


def is_same_chat_user(message, expected_chat_id, expected_user_id):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    return message.chat.id == expected_chat_id and user_id == expected_user_id


def _set_flow(chat_id, flow_name):
    _active_flows[chat_id] = {'name': flow_name, 'message_id': None}


def _set_flow_message(chat_id, message_id):
    flow = _active_flows.get(chat_id)
    if flow is not None:
        flow['message_id'] = message_id


def _clear_flow(chat_id):
    _active_flows.pop(chat_id, None)


def _check_flow(chat_id, expected_flow):
    flow = _active_flows.get(chat_id) or {}
    return flow.get('name') == expected_flow


def _get_flow_message_id(chat_id):
    flow = _active_flows.get(chat_id) or {}
    return flow.get('message_id')


def register_owned_next_step(sent_message, handler, expected_chat_id, expected_user_id, *handler_args):
    def wrapped(next_message):
        if not is_same_chat_user(next_message, expected_chat_id, expected_user_id):
            return
        handler(next_message, *handler_args)

    bot.register_next_step_handler_by_chat_id(expected_chat_id, wrapped)


def owner_only(handler):
    @wraps(handler)
    def wrapper(message, *args, **kwargs):
        if not is_owner(message):
            bot.reply_to(message, 'Sorry you are not allowed to use this command!')
            return
        return handler(message, *args, **kwargs)

    return wrapper


def auth_user_only(handler):
    @wraps(handler)
    def wrapper(message, *args, **kwargs):
        if not is_auth_user(message):
            bot.reply_to(message, 'Sorry you are not allowed to use this command!')
            return
        return handler(message, *args, **kwargs)

    return wrapper


def safe_command(handler):
    @wraps(handler)
    def wrapper(message, *args, **kwargs):
        try:
            return handler(message, *args, **kwargs)
        except Exception as exc:
            error_msg = '❌ Unexpected error occurred. Check logs for details.'
            logging.error('Error in %s: %s', handler.__name__, exc, exc_info=True)
            bot.reply_to(message, error_msg)

    return wrapper


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
            return
        except Exception as exc:
            logging.warning('Unable to update menu message %s in chat %s: %s', message_id, chat_id, exc)

    bot.send_message(
        chat_id,
        text,
        parse_mode='HTML',
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


def _home_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('📡 Plex Access', callback_data='nav_plex'),
        InlineKeyboardButton('🎬 Media', callback_data='nav_media'),
    )
    markup.add(InlineKeyboardButton('🔧 Maintenance', callback_data='nav_mw'))
    markup.add(InlineKeyboardButton('✖ Close', callback_data='menu_close'))
    return markup


def _plex_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('✅ Allow Plex', callback_data='plex_allow'),
        InlineKeyboardButton('🧹 Remove Access', callback_data='plex_reset'),
    )
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_home'))
    return markup


def _media_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton('📋 Pick Open Issue', callback_data='media_redownload'))
    seerr_browser_url = _get_seerr_browser_url()
    if seerr_browser_url:
        markup.add(InlineKeyboardButton('🌐 Open Overseerr', url=seerr_browser_url))
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_home'))
    return markup


def _maintenance_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('🙈 Silent Start', callback_data='mw_start_silent'),
        InlineKeyboardButton('📢 Regular Start', callback_data='mw_start_regular'),
    )
    markup.add(
        InlineKeyboardButton('🔁 Reboot 5m', callback_data='mw_reboot_default'),
        InlineKeyboardButton('💾 Firmware 5m', callback_data='mw_firmware_default'),
    )
    markup.add(
        InlineKeyboardButton('⏹ Silent Stop', callback_data='mw_stop_silent'),
        InlineKeyboardButton('✅ Stop + Notify', callback_data='mw_stop_regular'),
    )
    markup.add(
        InlineKeyboardButton('📋 Status', callback_data='mw_status'),
        InlineKeyboardButton('⬅ Back', callback_data='nav_home'),
    )
    return markup


def _plex_result_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('✅ Allow Plex', callback_data='plex_allow'),
        InlineKeyboardButton('🧹 Remove Access', callback_data='plex_reset'),
    )
    markup.add(
        InlineKeyboardButton('⬅ Back', callback_data='nav_plex'),
        InlineKeyboardButton('🏠 Home', callback_data='nav_home'),
    )
    return markup


def _media_result_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton('📋 Pick Open Issue', callback_data='media_redownload'))
    seerr_browser_url = _get_seerr_browser_url()
    if seerr_browser_url:
        markup.add(InlineKeyboardButton('🌐 Open Overseerr', url=seerr_browser_url))
    markup.add(
        InlineKeyboardButton('⬅ Back', callback_data='nav_media'),
        InlineKeyboardButton('🏠 Home', callback_data='nav_home'),
    )
    return markup


def _maintenance_result_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('📋 Status', callback_data='mw_status'),
        InlineKeyboardButton('⬅ Back', callback_data='nav_mw'),
    )
    markup.add(InlineKeyboardButton('🏠 Home', callback_data='nav_home'))
    return markup


def _show_home_menu(chat_id, user_id=None, message_id=None):
    display_user_id = user_id if user_id is not None else chat_id
    text = (
        '🤖 <b>MWBot</b>\n\n'
        f'<b>Your Telegram ID:</b> <code>{display_user_id}</code>\n'
        'Paste this into Seerr -> Notifications -> Telegram Chat ID.\n\n'
        '<b>Choose a section</b> to manage Plex, redownloads, or maintenance windows.'
    )
    _show_menu(
        chat_id,
        text,
        _home_markup(),
        message_id=message_id,
    )


def _show_plex_menu(chat_id, message_id=None):
    _show_menu(
        chat_id,
        '📡 <b>Plex Access</b>\n'
        'Allow Plex from your current location or remove the temporary rule when you are done.',
        _plex_markup(),
        message_id=message_id,
    )


def _show_media_menu(chat_id, message_id=None):
    _show_menu(
        chat_id,
        '🎬 <b>Media</b>\n'
        'Pick an open Seerr issue to replace a bad release, or jump into Overseerr first.',
        _media_markup(),
        message_id=message_id,
    )


def _show_maintenance_menu(chat_id, message_id=None):
    _show_menu(
        chat_id,
        '🔧 <b>Maintenance</b>\n'
        'Quick actions for the common MW flows.\n\n'
        '- Silent and regular starts stay open until you stop them\n'
        '- Reboot and firmware auto-stop after 5m\n'
        '- Use the buttons below for the common maintenance actions',
        _maintenance_markup(),
        message_id=message_id,
    )


def _show_plex_result(chat_id, text, message_id=None):
    _show_menu(
        chat_id,
        '📡 <b>Plex Access</b>\n' + escape(text),
        _plex_result_markup(),
        message_id=message_id,
    )


def _show_media_result(chat_id, text, message_id=None):
    _show_menu(
        chat_id,
        '🎬 <b>Redownload</b>\n' + escape(text),
        _media_result_markup(),
        message_id=message_id,
    )


def _show_maintenance_result(chat_id, text, message_id=None):
    _show_menu(
        chat_id,
        '🔧 <b>Maintenance</b>\n' + escape(text),
        _maintenance_result_markup(),
        message_id=message_id,
    )


def _start_silent_mw(duration=None, default_duration=None, reason='Silent maintenance window'):
    selected_duration = duration if duration is not None else default_duration
    result = start_mw()
    if result == 'MW has been started' and selected_duration is not None:
        replace_mw_state(bot, build_mw_state(selected_duration, reason=reason))
        return f'{result}. Timed stop scheduled in {format_duration(selected_duration)}.'
    return result


def _start_notified_mw(notification_text, duration=None, default_duration=None, reason='Maintenance window'):
    selected_duration = duration if duration is not None else default_duration
    result = start_mw()
    if result != 'MW has been started':
        return result

    notify_message = bot.send_message(chat_id=cfg.NOTIFY_CHAT_ID, text=notification_text)
    status = result + '. Sev1 chat has been notified'
    if selected_duration is None:
        return status

    state = build_mw_state(
        selected_duration,
        notify_chat_id=cfg.NOTIFY_CHAT_ID,
        notify_message_id=notify_message.message_id,
        reason=reason,
    )
    replace_mw_state(bot, state)
    return f'{status}. Timed stop scheduled in {format_duration(selected_duration)}.'


def _stop_silent_mw():
    result, _success = stop_timed_mw(bot)
    return result


def _stop_notified_mw():
    result, success = stop_timed_mw(bot)
    if success:
        bot.send_message(chat_id=cfg.NOTIFY_CHAT_ID, text='NAS: Server Status \nMaintenance window has been completed')
        return result + '. Sev1 chat has been notified'
    return result


# ── /start ───────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def command_start(message):
    _show_home_menu(message.chat.id, user_id=_get_user_id(message))


# ── /help ────────────────────────────────────────────────────────────

def _build_help_text():
    return '\n'.join([
        '<b>MWBot</b>',
        '',
        'Use /start for the main menu.',
        '',
        '<b>Quick Paths</b>',
        '/start — Open the main menu',
        '/help — Show this help',
        '',
        '<b>Shortcuts</b>',
        '/ip — Allow Plex from your current location',
        '/redownload — Replace a bad release from Seerr',
        '/mw — Open maintenance quick actions',
    ])


@bot.message_handler(commands=['help'])
def command_help(message):
    bot.send_message(
        message.chat.id,
        _build_help_text(),
        parse_mode='HTML',
    )


@bot.message_handler(commands=['mw'])
@safe_command
@owner_only
def command_mw_menu(message):
    _show_maintenance_menu(message.chat.id)


# ── /redownload ──────────────────────────────────────────────────────

@bot.message_handler(commands=['redownload'])
@safe_command
@auth_user_only
def command_redownload(message):
    _start_redownload_flow(message.chat.id, _get_user_id(message))


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
            markup.add(InlineKeyboardButton('Open Overseerr', url=seerr_browser_url))
        if message_id is not None:
            markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_media'))
            _show_menu(
                chat_id,
                '🎬 <b>Pick Open Issue</b>\n'
                'Choose the title with the bad release.\n'
                'If it is not listed, open Overseerr first and create a new issue.',
                markup,
                message_id=message_id,
            )
        else:
            markup.add(InlineKeyboardButton('Cancel', callback_data='cancel'))
            bot.send_message(
                chat_id,
                'Pick the title with the bad release.\nIf it is not listed, create a new issue in Overseerr first.',
                reply_markup=markup,
            )
    else:
        markup = InlineKeyboardMarkup(row_width=1)
        if seerr_browser_url:
            markup.add(InlineKeyboardButton('Open Overseerr', url=seerr_browser_url))
        if message_id is not None:
            markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_media'))
            _show_menu(
                chat_id,
                '🎬 <b>No Open Issues</b>\n'
                'Create a new issue in Overseerr, then come back here.',
                markup,
                message_id=message_id,
            )
        else:
            markup.add(InlineKeyboardButton('Cancel', callback_data='cancel'))
            bot.send_message(
                chat_id,
                'No open redownload issues right now.\nCreate a new issue in Overseerr, then come back here.',
                reply_markup=markup,
            )


# ── /ip ──────────────────────────────────────────────────────────────

@bot.message_handler(commands=['ip'])
@safe_command
@auth_user_only
def command_allow_cdn(message):
    _start_ip_flow(message.chat.id, _get_user_id(message))


def _start_ip_flow(chat_id, user_id, message_id=None):
    _set_flow(chat_id, 'ip')
    bot.send_chat_action(chat_id, 'typing')
    prompt_text = (
        '📡 <b>Allow Plex</b>\n'
        'Send your current IPv4 address.\n\n'
        'Visit <a href="https://ipinfo.io/ip">ipinfo.io/ip</a> and paste it here.'
    )
    if message_id is not None:
        _set_flow_message(chat_id, message_id)
        _show_menu(
            chat_id,
            prompt_text,
            _cancel_markup(cancel_callback='nav_plex', cancel_label='⬅ Back'),
            message_id=message_id,
        )
        sent = None
    else:
        sent = bot.send_message(
            chat_id,
            prompt_text,
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=_cancel_markup(),
        )
    register_owned_next_step(sent, ip, chat_id, user_id)


@bot.message_handler(commands=['reset_ip'])
@safe_command
@owner_only
def command_reset_cdn(message):
    bot.send_chat_action(message.chat.id, 'typing')
    result = disable_asn_to_firewall_rule()
    bot.send_message(message.chat.id, text=result or 'Unable to update firewall rule.')

def ip(message):
    if not _check_flow(message.chat.id, 'ip'):
        return
    flow_message_id = _get_flow_message_id(message.chat.id)
    _clear_flow(message.chat.id)

    ip_address = message.text
    if not is_valid_ip(ip_address):
        if flow_message_id is not None:
            _show_plex_result(message.chat.id, 'Invalid IP address format. Double-check it and try again.', message_id=flow_message_id)
        else:
            bot.send_message(message.chat.id, '❌ Invalid IP address format!\nDouble-check and rerun /ip')
    else:
        bot.send_chat_action(message.chat.id, 'typing')
        asn, error = get_asn_from_ip(ip_address)
        if asn is None:
            result_text = error or 'Unable to resolve ASN for this IP.'
            if flow_message_id is not None:
                _show_plex_result(message.chat.id, result_text, message_id=flow_message_id)
            else:
                bot.send_message(message.chat.id, text=result_text)
        else:
            result = add_asn_to_firewall_rule(asn)
            result_text = result or 'Unable to update firewall rule.'
            if flow_message_id is not None:
                _show_plex_result(message.chat.id, result_text, message_id=flow_message_id)
            else:
                bot.send_message(message.chat.id, text=result_text)


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
    _clear_flow(chat_id)
    key = _pending_key(chat_id, user_id)
    _pending_redownloads.pop(key, None)
    bot.clear_step_handler_by_chat_id(chat_id)
    bot.edit_message_reply_markup(
        chat_id=chat_id,
        message_id=call.message.message_id,
        reply_markup=None,
    )
    bot.answer_callback_query(call.id, text='Cancelled')


def _handle_menu_close(call):
    bot.answer_callback_query(call.id, text='Closed')
    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)


def _handle_nav_home(call):
    bot.answer_callback_query(call.id)
    _show_home_menu(call.message.chat.id, user_id=call.from_user.id, message_id=call.message.message_id)


def _handle_nav_plex(call):
    if not _require_auth_callback(call):
        return
    _show_plex_menu(call.message.chat.id, message_id=call.message.message_id)


def _handle_nav_media(call):
    if not _require_auth_callback(call):
        return
    _show_media_menu(call.message.chat.id, message_id=call.message.message_id)


def _handle_nav_mw(call):
    if not _require_owner_callback(call):
        return
    _show_maintenance_menu(call.message.chat.id, message_id=call.message.message_id)


def _handle_plex_allow(call):
    if not _require_auth_callback(call):
        return
    _start_ip_flow(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


def _handle_plex_reset(call):
    if not _require_owner_callback(call):
        return
    chat_id = call.message.chat.id
    bot.send_chat_action(chat_id, 'typing')
    result = disable_asn_to_firewall_rule()
    _show_plex_result(chat_id, result or 'Unable to update firewall rule.', message_id=call.message.message_id)


def _handle_media_redownload(call):
    if not _require_auth_callback(call):
        return
    _start_redownload_flow(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


def _handle_help(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, _build_help_text(), parse_mode='HTML')


def _handle_mw_action(call, result):
    _show_maintenance_result(call.message.chat.id, result, message_id=call.message.message_id)


def _handle_mw_start_silent(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_mw_action(call, _start_silent_mw())


def _handle_mw_start_regular(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_mw_action(call, _start_notified_mw('NAS: Server Status \nMaintenance window has been started.  \nThis may take awhile'))


def _handle_mw_reboot_default(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_mw_action(call, _start_notified_mw(
        'NAS: Server Status \nNAS is going to be rebooted. \nETA - 5 minutes',
        default_duration=DEFAULT_REBOOT_MW_DURATION,
        reason='Reboot maintenance',
    ))


def _handle_mw_firmware_default(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_mw_action(call, _start_notified_mw(
        'NAS: Server Status \nFirmware update. \nETA - 5 minutes',
        default_duration=DEFAULT_FIRMWARE_MW_DURATION,
        reason='Firmware maintenance',
    ))


def _handle_mw_stop_silent(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_mw_action(call, _stop_silent_mw())


def _handle_mw_stop_regular(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_mw_action(call, _stop_notified_mw())


def _handle_mw_status(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _handle_mw_action(call, get_mw_status_text())


CALLBACK_HANDLERS = {
    'cancel': _handle_cancel,
    'menu_close': _handle_menu_close,
    'nav_home': _handle_nav_home,
    'nav_plex': _handle_nav_plex,
    'nav_media': _handle_nav_media,
    'nav_mw': _handle_nav_mw,
    'cmd_mw': _handle_nav_mw,
    'plex_allow': _handle_plex_allow,
    'cmd_ip': _handle_plex_allow,
    'plex_reset': _handle_plex_reset,
    'media_redownload': _handle_media_redownload,
    'cmd_redownload': _handle_media_redownload,
    'cmd_help': _handle_help,
    'mw_start_silent': _handle_mw_start_silent,
    'mw_start_regular': _handle_mw_start_regular,
    'mw_reboot_default': _handle_mw_reboot_default,
    'mw_firmware_default': _handle_mw_firmware_default,
    'mw_stop_silent': _handle_mw_stop_silent,
    'mw_stop_regular': _handle_mw_stop_regular,
    'mw_status': _handle_mw_status,
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
        if target is None:
            bot.answer_callback_query(call.id, text='Session expired. Run /redownload again.')
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
        _show_media_result(chat_id, result or 'Redownload request completed.', message_id=call.message.message_id)
        return

    bot.answer_callback_query(call.id)


# ── Unknown command handler ──────────────────────────────────────────

@bot.message_handler(func=lambda message: is_command(message.text))
def command_unknown(message):
    command = str(message.text).split()[0]
    bot.reply_to(
        message, "Sorry, {} command not found!\nUse /help to see available commands.".format(command))


def main():
    warm_seerr_access_cache()
    register_bot_commands(bot)
    start_background_threads(bot)
    bot.infinity_polling()


if __name__ == '__main__':
    main()
