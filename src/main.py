import telebot
import cfg
import logging
import threading
from functools import wraps
from urllib.parse import urlparse
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from modules import (
    COMMANDS,
    HELP_SECTIONS,
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
    is_auth_user,
    is_command,
    is_owner,
    is_valid_ip,
    maintain_timed_mw,
    parse_duration,
    replace_mw_state,
    resolve_redownload_issue,
    schedule_fw_task,
    start_mw,
    stop_timed_mw,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

bot = telebot.TeleBot(cfg.TOKEN)

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
    _active_flows[chat_id] = flow_name


def _clear_flow(chat_id):
    _active_flows.pop(chat_id, None)


def _check_flow(chat_id, expected_flow):
    return _active_flows.get(chat_id) == expected_flow


def register_owned_next_step(sent_message, handler, expected_chat_id, expected_user_id, *handler_args):
    def wrapped(next_message):
        if not is_same_chat_user(next_message, expected_chat_id, expected_user_id):
            return
        handler(next_message, *handler_args)

    bot.register_next_step_handler(sent_message, wrapped)


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


def parse_duration_argument(message):
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2:
        return None, None
    return parse_duration(parts[1])


def _get_user_id(message):
    return getattr(getattr(message, 'from_user', None), 'id', None)


def _pending_key(chat_id, user_id):
    return f'{chat_id}:{user_id}'


def _get_seerr_browser_url():
    parsed = urlparse(cfg.SEERR_BASE_URL)
    if parsed.scheme == 'https' and parsed.netloc:
        return cfg.SEERR_BASE_URL
    if parsed.netloc == 'overseerr.freender.net':
        return f'https://{parsed.netloc}'
    return 'https://overseerr.freender.net'


# ── Inline Keyboard Helpers ──────────────────────────────────────────

def _cancel_markup():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton('Cancel', callback_data='cancel'))
    return markup


def _confirm_cancel_markup(confirm_data):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('Confirm', callback_data=confirm_data),
        InlineKeyboardButton('Cancel', callback_data='cancel'),
    )
    return markup


# ── /start ───────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def command_start(message):
    cid = message.chat.id
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton('📡 Allow Plex', callback_data='cmd_ip'),
        InlineKeyboardButton('🎬 Redownload', callback_data='cmd_redownload'),
    )
    markup.add(
        InlineKeyboardButton('📋 Commands', callback_data='cmd_help'),
        InlineKeyboardButton('🔧 MW Status', callback_data='cmd_mw_status'),
    )
    bot.send_message(
        cid,
        'Welcome to MWBot!',
        reply_markup=markup,
    )


# ── /help ────────────────────────────────────────────────────────────

def _build_help_text():
    lines = []
    for section in HELP_SECTIONS:
        lines.append(f"\n{section['icon']}  <b>{section['title']}</b>")
        for cmd, desc in section['commands'].items():
            lines.append(f'  /{cmd} — {desc}')
        if section.get('footer'):
            lines.append(f"  <i>{section['footer']}</i>")
    return '\n'.join(lines)


@bot.message_handler(commands=['help'])
def command_help(message):
    bot.send_message(
        message.chat.id,
        _build_help_text(),
        parse_mode='HTML',
    )


# ── /redownload ──────────────────────────────────────────────────────

@bot.message_handler(commands=['redownload'])
@safe_command
@auth_user_only
def command_redownload(message):
    _start_redownload_flow(message.chat.id, _get_user_id(message))


def _start_redownload_flow(chat_id, user_id):
    bot.send_chat_action(chat_id, 'typing')
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
        markup.add(InlineKeyboardButton('Open Overseerr', url=_get_seerr_browser_url()))
        markup.add(InlineKeyboardButton('Cancel', callback_data='cancel'))
        bot.send_message(
            chat_id,
            'Pick the title with the bad release.\nIf it is not listed, create a new issue in Overseerr first.',
            reply_markup=markup,
        )
    else:
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton('Open Overseerr', url=_get_seerr_browser_url()))
        bot.send_message(
            chat_id,
            'No open redownload issues right now.\nCreate a new issue in Overseerr, then come back here.',
            reply_markup=markup,
        )


# ── Maintenance Windows ──────────────────────────────────────────────

def start_notified_mw(message, notification_text):
    duration, error = parse_duration_argument(message)
    if error is not None:
        bot.reply_to(message, error)
        return

    bot.send_chat_action(message.chat.id, 'typing')
    result = start_mw()
    if result != 'MW has been started':
        bot.reply_to(message, result)
        return

    status = result + '. Sev1 chat has been notified'
    if duration is None:
        bot.reply_to(message, status)
        bot.send_message(chat_id=cfg.NOTIFY_CHAT_ID, text=notification_text)
        return

    notify_message = bot.send_message(chat_id=cfg.NOTIFY_CHAT_ID, text=notification_text)
    state = build_mw_state(
        duration,
        notify_chat_id=cfg.NOTIFY_CHAT_ID,
        notify_message_id=notify_message.message_id,
        reason='Maintenance window',
    )
    replace_mw_state(bot, state)
    bot.reply_to(message, f'{status}. Timed stop scheduled in {format_duration(duration)}.')


@bot.message_handler(commands=['start_silent'])
@safe_command
@owner_only
def command_start_silent(message):
    duration, error = parse_duration_argument(message)
    if error is not None:
        bot.reply_to(message, error)
        return
    bot.send_chat_action(message.chat.id, 'typing')
    result = start_mw()
    if result == 'MW has been started' and duration is not None:
        replace_mw_state(bot, build_mw_state(duration, reason='Silent maintenance window'))
        result = f'{result}. Timed stop scheduled in {format_duration(duration)}.'
    bot.reply_to(message, result)

@bot.message_handler(commands=['stop_silent'])
@safe_command
@owner_only
def command_stop_silent(message):
    bot.send_chat_action(message.chat.id, 'typing')
    result, _success = stop_timed_mw(bot)
    bot.reply_to(message, result)

@bot.message_handler(commands=['firmware_mw'])
@safe_command
@owner_only
def command_firmware_mw(message):
    start_notified_mw(message, 'NAS: Server Status \nFirmware update. \nETA - 15 minutes')

@bot.message_handler(commands=['reboot_mw'])
@safe_command
@owner_only
def command_reboot_mw(message):
    start_notified_mw(message, 'NAS: Server Status \nNAS is going to be rebooted. \nETA - 10 minutes')

@bot.message_handler(commands=['generic_mw'])
@safe_command
@owner_only
def command_generic_mw(message):
    start_notified_mw(message, 'NAS: Server Status \nMaintenance window has been started.  \nThis may take awhile')

@bot.message_handler(commands=['stop_mw'])
@safe_command
@owner_only
def command_stop_mw(message):
    bot.send_chat_action(message.chat.id, 'typing')
    result, success = stop_timed_mw(bot)
    if success:
        bot.send_message(chat_id=cfg.NOTIFY_CHAT_ID, text='NAS: Server Status \nMaintenance window has been completed')
        bot.reply_to(message, result + '. Sev1 chat has been notified')
        return
    bot.reply_to(message, result)


@bot.message_handler(commands=['mw_status'])
@owner_only
def command_mw_status(message):
    bot.reply_to(message, get_mw_status_text())


# ── /ip ──────────────────────────────────────────────────────────────

@bot.message_handler(commands=['ip'])
@auth_user_only
def command_allow_cdn(message):
    _start_ip_flow(message.chat.id, _get_user_id(message))


def _start_ip_flow(chat_id, user_id):
    _set_flow(chat_id, 'ip')
    bot.send_chat_action(chat_id, 'typing')
    sent = bot.send_message(
        chat_id,
        '📡 <b>Send your IPv4 address</b>\n\n'
        'Visit <a href="https://ipinfo.io/ip">ipinfo.io/ip</a> and paste the IP here.',
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
    _clear_flow(message.chat.id)

    ip_address = message.text
    if not is_valid_ip(ip_address):
        bot.send_message(message.chat.id, '❌ Invalid IP address format!\nDouble-check and rerun /ip')
    else:
        bot.send_chat_action(message.chat.id, 'typing')
        asn, error = get_asn_from_ip(ip_address)
        if asn is None:
            bot.send_message(message.chat.id, text=error or 'Unable to resolve ASN for this IP.')
        else:
            result = add_asn_to_firewall_rule(asn)
            bot.send_message(message.chat.id, text=result or 'Unable to update firewall rule.')


# ── Callback Queries (inline button presses) ────────────────────────

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    # Cancel — universal handler
    if data == 'cancel':
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
        return

    # Quick-action buttons from /start
    if data == 'cmd_ip':
        bot.answer_callback_query(call.id)
        if str(user_id) not in cfg.TELEGRAM_AUTH_USERS:
            bot.send_message(chat_id, 'Sorry you are not allowed to use this command!')
            return
        _start_ip_flow(chat_id, user_id)
        return

    if data == 'cmd_redownload':
        bot.answer_callback_query(call.id)
        if str(user_id) not in cfg.TELEGRAM_AUTH_USERS:
            bot.send_message(chat_id, 'Sorry you are not allowed to use this command!')
            return
        _start_redownload_flow(chat_id, user_id)
        return

    if data == 'cmd_help':
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, _build_help_text(), parse_mode='HTML')
        return

    if data == 'cmd_mw_status':
        bot.answer_callback_query(call.id)
        if user_id != cfg.OWNER:
            bot.send_message(chat_id, 'Sorry you are not allowed to use this command!')
            return
        bot.send_message(chat_id, get_mw_status_text())
        return

    # Redownload: user picked an issue from the list
    if data.startswith('redownload_issue:'):
        if str(user_id) not in cfg.TELEGRAM_AUTH_USERS:
            bot.answer_callback_query(call.id, text='Not authorized')
            return
        issue_id_str = data.split(':', 1)[1]
        bot.answer_callback_query(call.id)
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
        bot.send_chat_action(chat_id, 'typing')
        seerr_url = f'{cfg.SEERR_BASE_URL}/issues/{issue_id_str}'
        target, error = resolve_redownload_issue(seerr_url)
        if target is None:
            bot.send_message(chat_id, error or 'Unable to resolve Seerr issue.')
            return
        key = _pending_key(chat_id, user_id)
        _pending_redownloads[key] = target
        bot.send_message(
            chat_id,
            build_redownload_confirmation(target),
            reply_markup=_confirm_cancel_markup('redownload_confirm'),
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
        bot.edit_message_text(
            build_redownload_confirmation(target) + '\n\n⏳ Processing...',
            chat_id=chat_id,
            message_id=call.message.message_id,
        )
        bot.send_chat_action(chat_id, 'typing')
        result = execute_redownload(target)
        bot.edit_message_text(
            result or 'Redownload request completed.',
            chat_id=chat_id,
            message_id=call.message.message_id,
        )
        return

    bot.answer_callback_query(call.id)


# ── Unknown command handler ──────────────────────────────────────────

@bot.message_handler(func=lambda message: is_command(message.text))
def command_unknown(message):
    command = str(message.text).split()[0]
    bot.reply_to(
        message, "Sorry, {} command not found!\nUse /help to see available commands.".format(command))


def main():
    start_background_threads(bot)
    bot.infinity_polling()


if __name__ == '__main__':
    main()
