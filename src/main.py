import telebot
import cfg
import logging
import threading
from functools import wraps

from modules import (
    COMMANDS,
    add_asn_to_firewall_rule,
    build_mw_state,
    build_redownload_confirmation,
    disable_asn_to_firewall_rule,
    execute_redownload,
    format_duration,
    get_asn_from_ip,
    get_mw_status_text,
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


def start_background_threads(active_bot):
    scheduler_thread = threading.Thread(target=schedule_fw_task, daemon=True)
    scheduler_thread.start()

    timed_mw_thread = threading.Thread(target=maintain_timed_mw, args=(active_bot,), daemon=True)
    timed_mw_thread.start()


def is_same_chat_user(message, expected_chat_id, expected_user_id):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    return message.chat.id == expected_chat_id and user_id == expected_user_id


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


def start_notified_mw(message, notification_text):
    duration, error = parse_duration_argument(message)
    if error is not None:
        bot.reply_to(message, error)
        return

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


def handle_redownload_confirmation(message, target):
    response = (message.text or '').strip().lower()
    if response == 'cancel':
        bot.reply_to(message, 'Redownload request cancelled.')
        return
    if response != 'yes':
        sent = bot.reply_to(message, 'Please reply yes to continue or cancel to abort.')
        register_owned_next_step(
            sent,
            handle_redownload_confirmation,
            message.chat.id,
            getattr(getattr(message, 'from_user', None), 'id', None),
            target,
        )
        return

    result = execute_redownload(target)
    bot.reply_to(message, result or 'Redownload request completed.')


def handle_redownload_issue_url(message):
    url = message.text or ''
    target, error = resolve_redownload_issue(url)
    if target is None:
        bot.reply_to(message, error or 'Unable to resolve Seerr issue.')
        return

    sent = bot.reply_to(message, build_redownload_confirmation(target))
    register_owned_next_step(
        sent,
        handle_redownload_confirmation,
        message.chat.id,
        getattr(getattr(message, 'from_user', None), 'id', None),
        target,
    )

@bot.message_handler(commands=['start'])
def command_start(message):
    cid = message.chat.id
    bot.send_message(
        cid, "Welcome to MWBot!\nType /help to find all commands. Your cid identifier is " + str(cid))

@bot.message_handler(commands=['help'])
def command_help(message):
    cid = message.chat.id
    help_text = 'The following commands are available: \n'
    for key in COMMANDS:
        help_text += '/' + key + ': '
        help_text += COMMANDS[key] + '\n'
    bot.send_message(cid, help_text)


@bot.message_handler(commands=['redownload'])
@safe_command
@owner_only
def command_redownload(message):
    sent = bot.send_message(message.chat.id, 'Send a Seerr movie URL or an episode-linked Seerr issue URL to replace the current release.')
    register_owned_next_step(
        sent,
        handle_redownload_issue_url,
        message.chat.id,
        getattr(getattr(message, 'from_user', None), 'id', None),
    )

@bot.message_handler(commands=['start_silent'])
@safe_command
@owner_only
def command_start_silent(message):
    duration, error = parse_duration_argument(message)
    if error is not None:
        bot.reply_to(message, error)
        return
    result = start_mw()
    if result == 'MW has been started' and duration is not None:
        replace_mw_state(bot, build_mw_state(duration, reason='Silent maintenance window'))
        result = f'{result}. Timed stop scheduled in {format_duration(duration)}.'
    bot.reply_to(message, result)

@bot.message_handler(commands=['stop_silent'])
@safe_command
@owner_only
def command_stop_silent(message):
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


@bot.message_handler(commands=['ip'])
@auth_user_only
def command_allow_cdn(message):
    bot.send_chat_action(message.chat.id, 'typing')
    sent = bot.send_message(
        message.chat.id,
        'Please send your IPv4 address.\n\n'
        '🌐 To find your IP:\n'
        '1. Visit https://ipinfo.io/ip\n'
        '2. Copy the IP address shown\n'
        '3. Paste it here',
        disable_web_page_preview=True,
    )
    register_owned_next_step(
        sent,
        ip,
        message.chat.id,
        getattr(getattr(message, 'from_user', None), 'id', None),
    )

@bot.message_handler(commands=['reset_ip'])
@safe_command
@owner_only
def command_reset_cdn(message):
    result = disable_asn_to_firewall_rule()
    bot.send_message(message.chat.id, text=result or 'Unable to update firewall rule.')

def ip(message):
    ip_address = message.text
    if not is_valid_ip(ip_address):
        bot.send_message(message.chat.id, 'Invalid IP address format!\nDoublecheck and rerun /ip command')
    else:
        asn, error = get_asn_from_ip(ip_address)
        if asn is None:
            bot.send_message(message.chat.id, text=error or 'Unable to resolve ASN for this IP.')
        else:
            result = add_asn_to_firewall_rule(asn)
            bot.send_message(message.chat.id, text=result or 'Unable to update firewall rule.')

@bot.message_handler(func=lambda message: is_command(message.text))
def command_unknown(message):
    command = str(message.text).split()[0]
    bot.reply_to(
        message, "Sorry, {} command not found!\nPlease use /help to find all commands.".format(command))


def main():
    start_background_threads(bot)
    bot.infinity_polling()


if __name__ == '__main__':
    main()
