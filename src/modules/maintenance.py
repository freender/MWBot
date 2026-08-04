import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import cfg


ALERTMANAGER_STATE_FILE = '/config/alertmanager_mw_state.json'
STATE_LOCK = threading.Lock()
ALERTMANAGER_ACTION_LOCK = threading.RLock()

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
    os.makedirs(os.path.dirname(ALERTMANAGER_STATE_FILE), exist_ok=True)


def load_alertmanager_mw_state():
    with STATE_LOCK:
        if not os.path.exists(ALERTMANAGER_STATE_FILE):
            return None
        try:
            with open(ALERTMANAGER_STATE_FILE, 'r', encoding='utf-8') as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logging.error('Unable to read Alertmanager MW state: %s', exc)
            return None


def save_alertmanager_mw_state(state):
    with STATE_LOCK:
        _ensure_state_dir()
        temp_file = f'{ALERTMANAGER_STATE_FILE}.tmp'
        with open(temp_file, 'w', encoding='utf-8') as handle:
            json.dump(state, handle)
        os.replace(temp_file, ALERTMANAGER_STATE_FILE)


def clear_alertmanager_mw_state():
    with STATE_LOCK:
        try:
            if os.path.exists(ALERTMANAGER_STATE_FILE):
                os.remove(ALERTMANAGER_STATE_FILE)
            return True
        except OSError as exc:
            logging.error('Unable to clear Alertmanager MW state: %s', exc)
            return False


def start_alertmanager_mw(duration=None):
    with ALERTMANAGER_ACTION_LOCK:
        return _start_alertmanager_mw(duration)


def _start_alertmanager_mw(duration=None):
    if not cfg.ALERTMANAGER_URL:
        return 'Alertmanager maintenance is not configured.'

    from modules.alertmanager import create_silence, expire_silence, get_silence

    existing_state = load_alertmanager_mw_state()
    if existing_state:
        silence = get_silence(existing_state.get('silence_id'))
        silence_state = (silence or {}).get('status', {}).get('state')
        if silence_state in ('active', 'pending'):
            return get_alertmanager_mw_status_text(existing_state)
        if silence_state != 'expired':
            return 'Unable to verify the existing Alertmanager maintenance window.'

    selected_duration = duration or cfg.ALERTMANAGER_OPEN_MW_DURATION
    silence_id = create_silence(selected_duration, comment='mwbot-alertmanager-maintenance')
    if not silence_id:
        return 'Unable to start Alertmanager maintenance.'

    expires_at = datetime.now(ZoneInfo(cfg.TZ)) + selected_duration
    try:
        save_alertmanager_mw_state({
            'silence_id': silence_id,
            'expires_at': expires_at.isoformat(),
            'duration': format_duration(selected_duration),
        })
    except OSError as exc:
        logging.error('Unable to save Alertmanager MW state: %s', exc)
        if expire_silence(silence_id):
            return 'Unable to save Alertmanager maintenance state. The silence was rolled back.'
        return (
            'Alertmanager maintenance started, but its state could not be saved.\n'
            f"Safety expiry: {expires_at.strftime('%Y-%m-%d %H:%M %Z')}"
        )
    return (
        'Alertmanager maintenance started.\n'
        f"Safety expiry: {expires_at.strftime('%Y-%m-%d %H:%M %Z')}"
    )


def stop_alertmanager_mw():
    with ALERTMANAGER_ACTION_LOCK:
        return _stop_alertmanager_mw()


def _stop_alertmanager_mw():
    state = load_alertmanager_mw_state()
    if not state:
        return 'No Alertmanager maintenance window is active.'
    if not cfg.ALERTMANAGER_URL:
        return 'Alertmanager maintenance is not configured.'

    from modules.alertmanager import expire_silence

    if not expire_silence(state.get('silence_id')):
        return 'Unable to stop Alertmanager maintenance.'

    if not clear_alertmanager_mw_state():
        return 'Alertmanager maintenance completed, but local state cleanup failed.'
    return 'Alertmanager maintenance completed.'


def get_alertmanager_mw_status_text(state=None):
    with ALERTMANAGER_ACTION_LOCK:
        from modules.alertmanager import get_alertmanager_alert_status_text

        alert_status = get_alertmanager_alert_status_text()
        maintenance_status = _get_alertmanager_mw_status_text(state)
        return f'{alert_status}\n\n{maintenance_status}'


def _get_alertmanager_mw_status_text(state=None):
    active_state = state or load_alertmanager_mw_state()
    if not active_state:
        return 'No Alertmanager maintenance window is active.'
    if not cfg.ALERTMANAGER_URL:
        return 'Alertmanager maintenance is not configured.'

    expires_at = datetime.fromisoformat(active_state['expires_at'])
    remaining = expires_at - datetime.now(ZoneInfo(cfg.TZ))
    if remaining.total_seconds() <= 0:
        if not clear_alertmanager_mw_state():
            return 'Alertmanager maintenance expired, but local state cleanup failed.'
        return 'No Alertmanager maintenance window is active.'

    from modules.alertmanager import get_silence

    silence = get_silence(active_state.get('silence_id'))
    if not silence:
        return (
            'Unable to verify the Alertmanager maintenance window.\n'
            f"Local safety expiry: {expires_at.strftime('%Y-%m-%d %H:%M %Z')}"
        )

    silence_state = silence.get('status', {}).get('state')
    if silence_state == 'expired':
        if not clear_alertmanager_mw_state():
            return 'Alertmanager maintenance expired, but local state cleanup failed.'
        return 'No Alertmanager maintenance window is active.'

    return (
        'Alertmanager maintenance is active.\n'
        f"Safety expiry: {expires_at.strftime('%Y-%m-%d %H:%M %Z')}\n"
        f'Remaining: {format_duration(remaining)}'
    )
