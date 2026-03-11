import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

import pytz
import socketio.exceptions
from uptime_kuma_api import UptimeKumaApi, UptimeKumaException

import cfg


STATE_FILE = '/config/mw_state.json'
STATE_LOCK = threading.Lock()


def parse_duration(text):
    if not text:
        return None, None

    value = text.strip().lower()
    match = re.fullmatch(r'(\d+)([mh])', value)
    if not match:
        return None, 'Invalid duration. Use formats like 30m or 2h.'

    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        return None, 'Duration must be greater than zero.'

    if unit == 'm':
        return timedelta(minutes=amount), None
    return timedelta(hours=amount), None


def format_duration(delta):
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if hours:
        parts.append(f'{hours}h')
    if minutes or not parts:
        parts.append(f'{minutes}m')
    return ' '.join(parts)


def _ensure_state_dir():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)


def load_mw_state():
    with STATE_LOCK:
        if not os.path.exists(STATE_FILE):
            return None
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logging.error('Unable to read MW state: %s', exc)
            return None


def save_mw_state(state):
    with STATE_LOCK:
        _ensure_state_dir()
        with open(STATE_FILE, 'w', encoding='utf-8') as handle:
            json.dump(state, handle)


def delete_message(bot, chat_id, message_id):
    if not chat_id or not message_id:
        return
    try:
        bot.delete_message(chat_id, message_id)
    except Exception as exc:
        logging.warning('Unable to delete message %s in chat %s: %s', message_id, chat_id, exc)


def replace_mw_state(bot, state):
    previous_state = load_mw_state()
    if previous_state:
        logging.info(
            'Replacing timed MW state; deleting previous notification message %s in chat %s',
            previous_state.get('notify_message_id'),
            previous_state.get('notify_chat_id'),
        )
        delete_message(bot, previous_state.get('notify_chat_id'), previous_state.get('notify_message_id'))
    logging.info(
        'Saved timed MW state for %s ending at %s',
        state.get('reason') or 'Maintenance window',
        state.get('expires_at'),
    )
    save_mw_state(state)


def clear_mw_state():
    with STATE_LOCK:
        try:
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
        except OSError as exc:
            logging.error('Unable to clear MW state: %s', exc)


def build_mw_state(duration, notify_chat_id=None, notify_message_id=None, reason=None):
    expires_at = datetime.now(pytz.timezone(cfg.TZ)) + duration
    return {
        'expires_at': expires_at.isoformat(),
        'duration': format_duration(duration),
        'notify_chat_id': notify_chat_id,
        'notify_message_id': notify_message_id,
        'reason': reason,
    }


def get_mw_status_text(state=None):
    active_state = state or load_mw_state()
    if not active_state:
        return 'No timed maintenance window is active.'

    expires_at = datetime.fromisoformat(active_state['expires_at'])
    now = datetime.now(pytz.timezone(cfg.TZ))
    remaining = expires_at - now
    if remaining.total_seconds() <= 0:
        return 'Timed maintenance window has expired and is waiting for cleanup.'

    reason = active_state.get('reason') or 'Maintenance window'
    return (
        f'{reason} is active.\n'
        f"Ends at {expires_at.strftime('%Y-%m-%d %H:%M %Z')}\n"
        f'Remaining: {format_duration(remaining)}'
    )


def _run_kuma_maintenance_action(action_name, success_message, failure_message):
    try:
        api = UptimeKumaApi(cfg.KUMA_HOST)
        try:
            api.login(cfg.KUMA_LOGIN, cfg.KUMA_PASSWORD)
            getattr(api, action_name)(cfg.KUMA_MW_ID)
            return success_message
        except socketio.exceptions.TimeoutError:
            result = '⏱️ Timeout connecting to Uptime Kuma. Service may be slow or unreachable.'
            logging.error(result)
            return result
        except UptimeKumaException as exc:
            logging.error('%s: %s', failure_message, exc)
            return failure_message
        finally:
            try:
                api.disconnect()
            except Exception as exc:
                logging.error('Error while disconnecting: %s', exc)
    except socketio.exceptions.TimeoutError:
        result = '⏱️ Timeout connecting to Uptime Kuma. Service may be unreachable.'
        logging.error(result)
        return result
    except Exception as exc:
        result = 'Unable to establish connection to Uptime Kuma'
        logging.error('%s: %s', result, exc)
        return result


def start_mw():
    return _run_kuma_maintenance_action('resume_maintenance', 'MW has been started', 'An error occurred while resuming MW')


def stop_mw():
    return _run_kuma_maintenance_action('pause_maintenance', 'MW has been completed', 'An error occurred while pausing MW')


def stop_timed_mw(bot, notify_on_success=False):
    state = load_mw_state()
    if state:
        logging.info(
            'Stopping timed MW for %s scheduled to end at %s',
            state.get('reason') or 'Maintenance window',
            state.get('expires_at'),
        )
    result = stop_mw()
    success = result == 'MW has been completed'

    if success and state:
        delete_message(bot, state.get('notify_chat_id'), state.get('notify_message_id'))
        clear_mw_state()
        logging.info('Timed MW cleanup completed successfully')
        if notify_on_success and state.get('notify_chat_id'):
            bot.send_message(state['notify_chat_id'], 'Timed maintenance window has been completed.')
    elif state:
        logging.warning('Timed MW cleanup failed: %s', result)
    return result, success


def maintain_timed_mw(bot, poll_interval=30):
    while True:
        state = load_mw_state()
        if state:
            expires_at = datetime.fromisoformat(state['expires_at'])
            now = datetime.now(pytz.timezone(cfg.TZ))
            if expires_at <= now:
                logging.info(
                    'Timed MW expired for %s at %s; running cleanup',
                    state.get('reason') or 'Maintenance window',
                    expires_at.isoformat(),
                )
                result, success = stop_timed_mw(bot)
                if not success and state.get('notify_chat_id'):
                    bot.send_message(state['notify_chat_id'], f'Timed maintenance cleanup failed: {result}')
        time.sleep(poll_interval)
