import telebot
import cfg
import logging
import signal
import threading
import time
from datetime import datetime, timezone
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
    format_remaining,
    get_firewall_status_text,
    get_alertmanager_window_text,
    get_network_check,
    get_open_incident_index,
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

# -- Alerts currently listed for a user, keyed by "chat_id:user_id".  One stash for the
# whole alerts section: file, resolve and silence all index into the list that was drawn.
_pending_alerts = {}

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
        # One section, not two. Maintenance windows and incident filing were separate
        # menus over the same noun -- a firing alert -- so they are verbs in here now.
        markup.add(InlineKeyboardButton('🚨 Alerts', callback_data='nav_alerts'))
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


def _incident_result_markup(incident=None):
    markup = InlineKeyboardMarkup(row_width=2)
    if incident and incident.get('url'):
        markup.add(InlineKeyboardButton(f'Open Incident #{incident["number"]}', url=incident['url']))
    markup.add(
        InlineKeyboardButton('⬅ Alerts', callback_data='nav_alerts'),
        InlineKeyboardButton('🏠 Home', callback_data='nav_home'),
    )
    return markup


def _show_home_menu(chat_id, user_id=None, message_id=None):
    display_user_id = user_id if user_id is not None else chat_id
    has_auth_access = is_auth_chat_id(display_user_id)
    has_owner_access = is_owner_chat_id(display_user_id)
    if has_auth_access or has_owner_access:
        id_line = f'<b>Your Telegram ID:</b> <code>{display_user_id}</code>\n\n'
        body = '<b>Choose a section</b> to manage Plex, redownloads, or alerts.'
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


def _show_incident_result(chat_id, text, incident=None, message_id=None):
    _show_menu(
        chat_id,
        '🚨 <b>Homelab Incident</b>\n' + escape(text),
        _incident_result_markup(incident=incident),
        message_id=message_id,
    )


# ── Alerts ───────────────────────────────────────────────────────────
#
# A firing alert is one object with three possible answers: file it as an incident,
# clear it if it is a one-shot event, or mute it.  Those used to live in two top-level
# menus -- "Alertmanager MW" and "New Incident" -- which meant the same list of alerts
# was rendered three ways (as status text, as incident buttons, as resolve buttons) and
# no single view let you see an alert and act on it.
#
# Here the live list *is* the menu, and the actions hang off the alert you picked.

def _alert_incident_index(use_cache=True):
    """Open incidents keyed by alert fingerprint, or {} if we could not ask.

    Failure degrades the list to un-annotated alerts rather than failing the render:
    GitHub being unreachable is not a reason to stop showing what is on fire.
    """
    try:
        return get_open_incident_index(use_cache=use_cache)
    except Exception as exc:
        logging.warning('Unable to load open incidents for the alert list: %s', exc)
        return {}


def _load_alerts(chat_id, user_id):
    """Fetch the alert list and stash it for the callbacks that index into it."""
    from modules.alertmanager import get_alert_choices

    alerts = get_alert_choices()
    if alerts is not None:
        _pending_alerts[_pending_key(chat_id, user_id)] = alerts
    return alerts


def _pending_alert(chat_id, user_id, index_text):
    alerts = _pending_alerts.get(_pending_key(chat_id, user_id))
    try:
        return alerts[int(index_text)]
    except (TypeError, ValueError, IndexError):
        return None


def _alert_silence_remaining(alert, silences=None):
    """How long our silence on this alert still has to run, e.g. '6d'."""
    from modules.alertmanager import alert_silenced_until

    ends_at = alert_silenced_until(alert, index=silences)
    if ends_at is None:
        return None
    return format_remaining(ends_at - datetime.now(timezone.utc))


def _alert_list_button(alert, index, incidents, silences=None):
    from modules.alertmanager import alert_button_label, alert_fingerprint

    incident = incidents.get(alert_fingerprint(alert))
    # The "→ #N" is the whole point of annotating here: an alert that is already filed
    # should say so on the list, not after you have tapped File Incident and been told
    # a duplicate was refused.
    suffix = f' → #{incident["number"]}' if incident else ''
    # A week-long silence is the one most likely to be forgotten, so the row it hides
    # behind says when it lapses. Without this, silencing removes the alert from your
    # attention and nothing puts it back.
    remaining = _alert_silence_remaining(alert, silences=silences)
    if remaining:
        suffix += f' 🔇 {remaining}'
    return InlineKeyboardButton(
        alert_button_label(alert, limit=max(24, 48 - len(suffix))) + suffix,
        callback_data=f'alert_pick:{index}',
    )


def _alert_silence_index(alerts):
    """One silence lookup for the whole list, and only when something is suppressed."""
    from modules.alertmanager import silence_index

    if not any((alert.get('status') or {}).get('silencedBy') for alert in alerts or []):
        return {}
    return silence_index()


def _show_alerts_menu(chat_id, user_id, message_id=None, notice=None):
    from modules.alertmanager import format_alert_summary

    bot.send_chat_action(chat_id, 'typing')
    alerts = _load_alerts(chat_id, user_id)
    incidents = _alert_incident_index() if alerts else {}
    silences = _alert_silence_index(alerts)
    # Read before the buttons are built: this also clears a window whose silence has
    # already expired, so the footer offers Start rather than End for a dead window.
    window = get_alertmanager_window_text()

    lines = ['🚨 <b>Alerts</b>']
    if notice:
        lines.append(escape(notice))
    lines.append(escape(format_alert_summary(alerts)))
    if window:
        lines.append(escape(window))
    if alerts:
        lines.append('')
        lines.append('Pick an alert to file, resolve, or silence it.')

    markup = InlineKeyboardMarkup(row_width=1)
    for index, alert in enumerate(alerts or []):
        markup.add(_alert_list_button(alert, index, incidents, silences=silences))

    if window:
        maintenance_button = InlineKeyboardButton('⏹ End Maintenance', callback_data='am_mw_stop')
    else:
        maintenance_button = InlineKeyboardButton(
            f'🔕 Maintenance {format_duration(cfg.ALERTMANAGER_OPEN_MW_DURATION)}',
            callback_data='am_mw_start',
        )
    markup.row(
        maintenance_button,
        InlineKeyboardButton('🔄 Refresh', callback_data='nav_alerts'),
    )
    markup.add(InlineKeyboardButton('🏠 Home', callback_data='nav_home'))
    _show_menu(chat_id, '\n'.join(lines), markup, message_id=message_id)


def _show_alert_actions(chat_id, user_id, index_text, message_id=None):
    """The action sheet for one alert."""
    from modules.alertmanager import (
        alert_fingerprint,
        build_alert_incident_text,
        is_resolvable,
    )

    alert = _pending_alert(chat_id, user_id, index_text)
    if alert is None:
        _show_alerts_menu(
            chat_id,
            user_id,
            message_id=message_id,
            notice='That alert list expired; this is the current one.',
        )
        return

    incident = _alert_incident_index().get(alert_fingerprint(alert))
    # Deliberately the same text that would be filed, so what you read here is exactly
    # what triage receives -- no second rendering to drift out of step with the first.
    lines = ['🚨 <b>Alert</b>', escape(build_alert_incident_text(alert))]

    markup = InlineKeyboardMarkup(row_width=2)
    if incident:
        lines.append('')
        lines.append(f'Already filed as #{escape(str(incident["number"]))}.')
        if incident.get('url'):
            markup.add(InlineKeyboardButton(
                f'🔗 Open Incident #{incident["number"]}',
                url=incident['url'],
            ))
    else:
        markup.add(InlineKeyboardButton(
            '🚨 File Incident',
            callback_data=f'alert_incident:{index_text}',
        ))

    actions = []
    remaining = _alert_silence_remaining(alert)
    if remaining:
        lines.append('')
        lines.append(f'🔇 Silenced by MWBot — {escape(remaining)} left.')
        actions.append(InlineKeyboardButton(
            '🔔 Unsilence',
            callback_data=f'alert_unsilence:{index_text}',
        ))
    else:
        actions.append(InlineKeyboardButton(
            '🔕 Silence',
            callback_data=f'alert_silence:{index_text}',
        ))
    # Offered only for one-shot events. A metric alert would be re-sent by vmalert within
    # one evaluation interval, making the button look broken rather than declined.
    if is_resolvable(alert):
        actions.append(InlineKeyboardButton(
            '✅ Resolve',
            callback_data=f'alert_resolve:{index_text}',
        ))
    markup.row(*actions)
    markup.add(InlineKeyboardButton('⬅ Back', callback_data='nav_alerts'))
    _show_menu(chat_id, '\n'.join(lines), markup, message_id=message_id)


def _show_silence_picker(chat_id, user_id, index_text, message_id=None):
    """Ask how long before silencing, rather than assuming one duration fits.

    A fixed duration cannot serve both "quiet until tomorrow" and "quiet while a disk is
    on order": the second one would need re-silencing every day, which is the daily
    interruption it was meant to stop.
    """
    from modules.alertmanager import alert_button_label

    alert = _pending_alert(chat_id, user_id, index_text)
    if alert is None:
        _show_alerts_menu(chat_id, user_id, message_id=message_id,
                          notice='That alert list expired; this is the current one.')
        return

    markup = InlineKeyboardMarkup(row_width=len(cfg.ALERTMANAGER_ALERT_SILENCE_DURATIONS))
    markup.row(*[
        InlineKeyboardButton(
            format_duration(duration),
            callback_data=f'alert_silence_do:{index_text}:{choice}',
        )
        for choice, duration in enumerate(cfg.ALERTMANAGER_ALERT_SILENCE_DURATIONS)
    ])
    markup.add(InlineKeyboardButton('⬅ Back', callback_data=f'alert_pick:{index_text}'))
    _show_menu(
        chat_id,
        '🔕 <b>Silence Alert</b>\n'
        + escape(alert_button_label(alert, limit=120))
        + '\n\nFor how long? Nothing re-notifies when a silence lapses, '
        'so the alert simply becomes audible again.',
        markup,
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


def _show_incident_picker(chat_id, user_id, message_id=None):
    """The direct filing list behind /incident.

    The Alerts menu reaches filing in two taps -- pick the alert, then File Incident --
    because it also offers resolve and silence on the way.  /incident is the one-tap
    path for when filing is already the decision, so its buttons file straight away.

    Incidents are filed from a firing Alertmanager alert only.  Free-text reports are
    deliberately unsupported: the issue body is what the triage agent reasons over, so
    it stays machine-generated and consistent.
    """
    if not incident_creation_is_configured():
        _show_incident_result(chat_id, 'GitHub incident creation is not configured.', message_id=message_id)
        return

    bot.send_chat_action(chat_id, 'typing')
    alerts = _load_alerts(chat_id, user_id)
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

    incidents = _alert_incident_index()
    markup = InlineKeyboardMarkup(row_width=1)
    for index, alert in enumerate(alerts):
        button = _alert_list_button(alert, index, incidents)
        button.callback_data = f'alert_incident:{index}'
        markup.add(button)
    markup.add(InlineKeyboardButton('⬅ Alerts', callback_data='nav_alerts'))
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
    _show_incident_picker(message.chat.id, user_id)


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
    _pending_alerts.pop(key, None)
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


def _handle_nav_alerts(call):
    if not _require_owner_callback(call):
        return
    _show_alerts_menu(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


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


def _handle_alertmanager_mw_start(call):
    if not _require_owner_callback(call):
        return
    # Every alert action lands back on the list with what happened as a notice, so the
    # result and the state it produced are the same screen.
    _show_alerts_menu(
        call.message.chat.id,
        call.from_user.id,
        message_id=call.message.message_id,
        notice=start_alertmanager_mw(),
    )


def _handle_alertmanager_mw_stop(call):
    if not _require_owner_callback(call):
        return
    _show_alerts_menu(
        call.message.chat.id,
        call.from_user.id,
        message_id=call.message.message_id,
        notice=stop_alertmanager_mw(),
    )


def _handle_alert_pick(call, index_text):
    if not _require_owner_callback(call):
        return
    bot.send_chat_action(call.message.chat.id, 'typing')
    _show_alert_actions(
        call.message.chat.id,
        call.from_user.id,
        index_text,
        message_id=call.message.message_id,
    )


def _handle_alert_incident(call, index_text):
    """File an incident straight from the selected alert."""
    if not _require_owner_callback(call):
        return
    chat_id = call.message.chat.id
    alert = _pending_alert(chat_id, call.from_user.id, index_text)
    if alert is None:
        _show_incident_result(
            chat_id,
            'That alert list expired. Open Alerts again.',
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


def _handle_alert_silence(call, index_text):
    if not _require_owner_callback(call):
        return
    _show_silence_picker(
        call.message.chat.id,
        call.from_user.id,
        index_text,
        message_id=call.message.message_id,
    )


def _handle_alert_silence_confirm(call, data_text):
    """`<alert index>:<duration choice>` -- the alert picked, then how long for."""
    if not _require_owner_callback(call):
        return
    from modules.alertmanager import alert_button_label, silence_alert

    chat_id = call.message.chat.id
    user_id = call.from_user.id
    index_text, _, choice_text = data_text.partition(':')
    try:
        duration = cfg.ALERTMANAGER_ALERT_SILENCE_DURATIONS[int(choice_text)]
    except (TypeError, ValueError, IndexError):
        _show_alerts_menu(chat_id, user_id, message_id=call.message.message_id,
                          notice='That silence duration is no longer offered.')
        return

    bot.send_chat_action(chat_id, 'typing')
    alert = _pending_alert(chat_id, user_id, index_text)
    if alert is None:
        _show_alerts_menu(chat_id, user_id, message_id=call.message.message_id,
                          notice='That alert list expired; this is the current one.')
        return

    label = alert_button_label(alert, limit=120)
    if silence_alert(alert, duration):
        notice = f'Silenced for {format_duration(duration)}: {label}'
    else:
        notice = f'Unable to silence: {label}'
    _show_alerts_menu(chat_id, user_id, message_id=call.message.message_id, notice=notice)


def _handle_alert_unsilence(call, index_text):
    if not _require_owner_callback(call):
        return
    from modules.alertmanager import alert_button_label, unsilence_alert

    chat_id = call.message.chat.id
    bot.send_chat_action(chat_id, 'typing')
    alert = _pending_alert(chat_id, call.from_user.id, index_text)
    if alert is None:
        _show_alerts_menu(chat_id, call.from_user.id, message_id=call.message.message_id,
                          notice='That alert list expired; this is the current one.')
        return

    label = alert_button_label(alert, limit=120)
    if unsilence_alert(alert):
        notice = f'Unsilenced: {label}'
    else:
        # Either nothing of ours was silencing it, or expiring a silence failed. Both
        # leave it audible or not on our say-so, so neither claims success.
        notice = f'No silence of ours to lift on: {label}'
    _show_alerts_menu(chat_id, call.from_user.id, message_id=call.message.message_id, notice=notice)


def _handle_alert_resolve(call, index_text):
    """Confirm before clearing, since a resolved alert cannot be brought back."""
    if not _require_owner_callback(call):
        return
    from modules.alertmanager import alert_button_label

    chat_id = call.message.chat.id
    alert = _pending_alert(chat_id, call.from_user.id, index_text)
    if alert is None:
        _show_alerts_menu(chat_id, call.from_user.id, message_id=call.message.message_id,
                          notice='That alert list expired; this is the current one.')
        return

    _show_menu(
        chat_id,
        '✅ <b>Resolve Alert</b>\n'
        + escape(alert_button_label(alert, limit=120))
        + '\n\nClearing only removes the alert from Alertmanager. '
        'It does not fix the underlying event.',
        _confirm_cancel_markup(
            f'alert_resolve_do:{index_text}',
            confirm_label='Resolve',
            cancel_callback=f'alert_pick:{index_text}',
            cancel_label='⬅ Back',
        ),
        message_id=call.message.message_id,
    )


def _handle_alert_resolve_confirm(call, index_text):
    if not _require_owner_callback(call):
        return
    from modules.alertmanager import alert_button_label, resolve_alert

    chat_id = call.message.chat.id
    bot.send_chat_action(chat_id, 'typing')
    alert = _pending_alert(chat_id, call.from_user.id, index_text)
    if alert is None:
        _show_alerts_menu(chat_id, call.from_user.id, message_id=call.message.message_id,
                          notice='That alert list expired; this is the current one.')
        return

    label = alert_button_label(alert, limit=120)
    notice = f'Resolved: {label}' if resolve_alert(alert) else f'Unable to resolve: {label}'
    _show_alerts_menu(chat_id, call.from_user.id, message_id=call.message.message_id, notice=notice)


def _handle_incident_new(call):
    if not _require_owner_callback(call):
        return
    _show_incident_picker(call.message.chat.id, call.from_user.id, message_id=call.message.message_id)


CALLBACK_HANDLERS = {
    'cancel': _handle_cancel,
    'menu_close': _handle_menu_close,
    'nav_home': _handle_nav_home,
    'nav_plex': _handle_nav_plex,
    'nav_media': _handle_nav_media,
    'nav_alerts': _handle_nav_alerts,
    # Retired home-menu buttons. Telegram keeps old menu messages in the chat forever, and
    # a tap on one would otherwise do nothing at all; both land in the section that
    # replaced them.
    'nav_am_mw': _handle_nav_alerts,
    'incident_new': _handle_incident_new,
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
}

# Actions on a specific alert, carrying its index into the list drawn for that user.
# Membership here is what makes a callback owner-only -- see `_is_owner_only_callback` --
# so every entry must be an alert action and nothing else belongs in this map.
ALERT_CALLBACK_HANDLERS = {
    'alert_pick:': _handle_alert_pick,
    'alert_incident:': _handle_alert_incident,
    'alert_silence:': _handle_alert_silence,
    'alert_silence_do:': _handle_alert_silence_confirm,
    'alert_unsilence:': _handle_alert_unsilence,
    'alert_resolve:': _handle_alert_resolve,
    'alert_resolve_do:': _handle_alert_resolve_confirm,
}

# Every fixed-name owner-only callback. The monitoring surface is the bulk of it -- the
# Alerts section, the blanket maintenance window, incident filing -- plus revoking Plex
# access, which is owner-only for the same reason: it changes state for other people.
# Per-alert actions are not repeated here; ALERT_CALLBACK_HANDLERS above is owner-only
# in its entirety.
OWNER_ONLY_CALLBACKS = frozenset({
    'nav_alerts',
    'nav_am_mw',
    'incident_new',
    'am_mw_start',
    'am_mw_stop',
    'plex_reset',
})


def _is_owner_only_callback(data):
    """True for callbacks only the owner may run, monitoring above all.

    Registration is what gates a callback, not a line inside its handler. An Alertmanager
    silence or resolution changes what the homelab will tell anyone about itself, and
    filing an incident writes to a private repo and triggers a triage run against real
    hosts. A new alert action must not be able to reach any of that by forgetting
    `_require_owner_callback`.
    """
    return data in OWNER_ONLY_CALLBACKS or any(
        data.startswith(prefix) for prefix in ALERT_CALLBACK_HANDLERS
    )


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    # Checked before dispatch, so a non-owner never reaches a monitoring handler at all.
    # The handlers still check for themselves -- they are also called directly -- but this
    # is the check that cannot be omitted by writing a new one.
    if _is_owner_only_callback(data) and not is_owner_chat_id(user_id):
        bot.answer_callback_query(call.id)
        _answer_not_allowed(chat_id)
        return

    handler = CALLBACK_HANDLERS.get(data)
    if handler is not None:
        handler(call)
        return

    for prefix, indexed_handler in ALERT_CALLBACK_HANDLERS.items():
        if data.startswith(prefix):
            indexed_handler(call, data.split(':', 1)[1])
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
