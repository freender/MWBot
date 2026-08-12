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
    find_triage_reports,
    format_duration,
    get_firewall_status_text,
    get_alertmanager_mw_status_text,
    get_network_check,
    get_open_seerr_issues,
    get_owner_chat_ids,
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

# -- Firing alerts offered by the incident picker, keyed by "chat_id:user_id" --
_pending_incident_alerts = {}

# -- One-shot alerts offered by the resolve picker, keyed by "chat_id:user_id" --
_pending_resolve_alerts = {}

# -- Latest menu message keyed by chat_id -- keeps /start tidy by replacing old menu messages
_home_menu_messages = {}


def _announce_triage_report(report):
    """Tell the owner an incident has been diagnosed, and link to it.

    The message deliberately carries no fix button. Fixing an incident deploys to homelab
    hosts, and the triage repo authorises that by requiring a comment from the repository
    owner on GitHub. MWBot holds an owner token, so a button here would quietly relocate
    that authority into this chat. See modules/incidents.py for the full reasoning.

    Sent only to the owner's own chat(s) -- the same identity that gates /incident, which
    is what filed the issue in the first place -- and never cfg.CHAT_ID. That is a shared
    alert-broadcast chat with other people in it, and a prompt to go and authorise a deploy
    has no business landing where anyone but the owner can read it.
    """
    issue = report.get('issue')
    markup = InlineKeyboardMarkup(row_width=1)
    if report.get('url'):
        markup.add(InlineKeyboardButton(f'Read triage on #{issue}', url=report['url']))
    owner_chat_ids = get_owner_chat_ids()
    if not owner_chat_ids:
        logging.error(
            'No owner chat id resolved; cannot announce triage report for incident #%s',
            issue,
        )
        return
    for chat_id in owner_chat_ids:
        bot.send_message(
            chat_id,
            '🔎 <b>Triage complete</b>\n'
            f'Incident #{escape(str(issue))} has a report waiting.\n'
            'Read it, and reply <code>/fix</code> on GitHub if the fix looks right.',
            reply_markup=markup,
            parse_mode='HTML',
        )


def watch_triage_reports(shutdown_event=None):
    """Poll the triage repo for incidents that have been diagnosed. Read-only."""
    interval = cfg.GITHUB_TRIAGE_WATCH_SECONDS
    if interval <= 0 or not incident_creation_is_configured():
        logging.info('Triage-report watcher disabled')
        return

    while shutdown_event is None or not shutdown_event.is_set():
        try:
            for report in find_triage_reports():
                logging.info('Triage report posted for incident #%s', report['issue'])
                _announce_triage_report(report)
        except Exception as exc:
            # A watcher that dies takes its notifications with it silently; keep polling.
            logging.error('Triage-report watch failed: %s', exc, exc_info=True)
        if shutdown_event is None:
            time.sleep(interval)
        elif shutdown_event.wait(interval):
            break


def start_background_threads(active_bot):
    scheduler_thread = threading.Thread(target=schedule_fw_task, args=(shutdown_event,), daemon=True)
    scheduler_thread.start()
    triage_watch_thread = threading.Thread(
        target=watch_triage_reports, args=(shutdown_event,), daemon=True)
    triage_watch_thread.start()


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
        InlineKeyboardButton('✅ Resolve Alert', callback_data='am_resolve'),
    )
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_home'))
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
        '- Stop completes the active Alertmanager window\n'
        '- Resolve clears a one-shot event alert that will never clear itself',
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


def _create_incident_from_telegram(chat_id, summary, fingerprint=None, message_id=None):
    bot.send_chat_action(chat_id, 'typing')
    incident, error = create_incident(summary, fingerprint=fingerprint)
    if incident is None:
        _show_incident_result(chat_id, error or 'Unable to create the incident.', message_id=message_id)
        return
    if incident.get('duplicate'):
        # Nothing was filed and no triage was requested: this alert already has an open
        # incident.  Say so plainly rather than reporting a creation that did not happen.
        _show_incident_result(
            chat_id,
            f'Already filed as #{incident["number"]}. Nothing new was created.',
            incident=incident,
            message_id=message_id,
        )
        return
    _show_incident_result(
        chat_id,
        f'Incident #{incident["number"]} created. OpenCode triage is queued.',
        incident=incident,
        message_id=message_id,
    )


def _start_incident_flow(chat_id, user_id, message_id=None):
    """Incidents are filed from a firing Alertmanager alert only.

    Free-text reports are deliberately unsupported: the issue body is what the triage
    agent reasons over, so it stays machine-generated and consistent.
    """
    if not incident_creation_is_configured():
        _show_incident_result(chat_id, 'GitHub incident creation is not configured.', message_id=message_id)
        return

    from modules.alertmanager import alert_button_label, get_incident_alert_choices

    bot.send_chat_action(chat_id, 'typing')
    alerts = get_incident_alert_choices()
    if alerts is None:
        _show_incident_result(
            chat_id,
            'Alertmanager is unavailable, so no alert can be filed right now.',
            message_id=message_id,
        )
        return
    if not alerts:
        _show_incident_result(
            chat_id,
            'Nothing is firing. Incidents are filed from an active alert.',
            message_id=message_id,
        )
        return

    _pending_incident_alerts[_pending_key(chat_id, user_id)] = alerts
    markup = InlineKeyboardMarkup()
    for index, alert in enumerate(alerts):
        markup.add(InlineKeyboardButton(
            alert_button_label(alert),
            callback_data=f'incident_alert:{index}',
        ))
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_home'))
    _show_menu(
        chat_id,
        '🚨 <b>New Incident</b>\nPick the firing alert to file.',
        markup,
        message_id=message_id,
    )


@bot.message_handler(commands=['incident'])
def command_incident(message):
    user_id = _get_user_id(message)
    if not is_owner_chat_id(user_id):
        _answer_not_allowed(message.chat.id)
        return
    _start_incident_flow(message.chat.id, user_id)


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
    _pending_incident_alerts.pop(key, None)
    _pending_resolve_alerts.pop(key, None)
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


def _start_resolve_flow(chat_id, user_id, message_id=None):
    """Offer the one-shot alerts that can be dismissed by hand."""
    from modules.alertmanager import alert_button_label, get_resolvable_alert_choices

    alerts = get_resolvable_alert_choices()
    if alerts is None:
        _show_alertmanager_mw_result(
            chat_id,
            'Alertmanager is unavailable, so no alert can be resolved right now.',
            message_id=message_id,
        )
        return

    if not alerts:
        _show_alertmanager_mw_result(
            chat_id,
            'Nothing to resolve. Only one-shot event alerts can be cleared by hand; '
            'metric-based alerts clear themselves once the condition ends.',
            message_id=message_id,
        )
        return

    _pending_resolve_alerts[_pending_key(chat_id, user_id)] = alerts
    markup = InlineKeyboardMarkup(row_width=1)
    for index, alert in enumerate(alerts):
        markup.add(
            InlineKeyboardButton(
                alert_button_label(alert),
                callback_data=f'am_resolve_pick:{index}',
            )
        )
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_am_mw'))
    _show_menu(
        chat_id,
        '✅ <b>Resolve Alert</b>\nPick the event alert to clear.',
        markup,
        message_id=message_id,
    )


def _pop_pending_resolve_alert(chat_id, user_id, index_text, keep=False):
    key = _pending_key(chat_id, user_id)
    alerts = _pending_resolve_alerts.get(key)
    try:
        alert = alerts[int(index_text)]
    except (TypeError, ValueError, IndexError):
        _pending_resolve_alerts.pop(key, None)
        return None
    if not keep:
        _pending_resolve_alerts.pop(key, None)
    return alert


def _handle_alertmanager_resolve(call):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _start_resolve_flow(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


def _handle_alertmanager_resolve_pick(call, index_text):
    """Confirm before clearing, since a resolved alert cannot be brought back."""
    if not _require_owner_callback(call):
        return
    from modules.alertmanager import alert_button_label

    chat_id = call.message.chat.id
    alert = _pop_pending_resolve_alert(chat_id, call.from_user.id, index_text, keep=True)
    if alert is None:
        _show_alertmanager_mw_result(
            chat_id,
            'That alert list expired. Open Resolve Alert again.',
            message_id=call.message.message_id,
        )
        return

    _show_menu(
        chat_id,
        '✅ <b>Resolve Alert</b>\n'
        + escape(alert_button_label(alert, limit=120))
        + '\n\nClearing only removes the alert from Alertmanager. '
        'It does not fix the underlying event.',
        _confirm_cancel_markup(f'am_resolve_do:{index_text}', confirm_label='Resolve'),
        message_id=call.message.message_id,
    )


def _handle_alertmanager_resolve_confirm(call, index_text):
    if not _require_owner_callback(call):
        return
    from modules.alertmanager import alert_button_label, resolve_alert

    chat_id = call.message.chat.id
    bot.send_chat_action(chat_id, 'typing')
    alert = _pop_pending_resolve_alert(chat_id, call.from_user.id, index_text)
    if alert is None:
        _show_alertmanager_mw_result(
            chat_id,
            'That alert list expired. Open Resolve Alert again.',
            message_id=call.message.message_id,
        )
        return

    label = alert_button_label(alert, limit=120)
    if resolve_alert(alert):
        text = f'Resolved: {label}'
    else:
        text = f'Unable to resolve: {label}'
    _show_alertmanager_mw_result(chat_id, text, message_id=call.message.message_id)


def _handle_incident_new(call):
    if not _require_owner_callback(call):
        return
    _start_incident_flow(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


def _handle_incident_alert_choice(call, index_text):
    """Create an incident straight from a selected firing alert."""
    if not _require_owner_callback(call):
        return
    chat_id = call.message.chat.id
    alerts = _pending_incident_alerts.pop(_pending_key(chat_id, call.from_user.id), None)
    try:
        alert = alerts[int(index_text)]
    except (TypeError, ValueError, IndexError):
        _show_incident_result(
            chat_id,
            'That alert list expired. Run /incident again.',
            message_id=call.message.message_id,
        )
        return

    from modules.alertmanager import alert_fingerprint, build_alert_incident_text

    _create_incident_from_telegram(
        chat_id,
        build_alert_incident_text(alert),
        fingerprint=alert_fingerprint(alert),
        message_id=call.message.message_id,
    )


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
    'am_resolve': _handle_alertmanager_resolve,
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

    # Incident: owner picked one of the firing alerts
    if data.startswith('incident_alert:'):
        _handle_incident_alert_choice(call, data.split(':', 1)[1])
        return

    # Resolve: owner picked a one-shot alert, then confirmed clearing it
    if data.startswith('am_resolve_pick:'):
        _handle_alertmanager_resolve_pick(call, data.split(':', 1)[1])
        return

    if data.startswith('am_resolve_do:'):
        _handle_alertmanager_resolve_confirm(call, data.split(':', 1)[1])
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
