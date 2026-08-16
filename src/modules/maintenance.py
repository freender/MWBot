import json
import logging
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import cfg


ALERTMANAGER_STATE_FILE = '/config/alertmanager_mw_state.json'
STATE_LOCK = threading.Lock()
ALERTMANAGER_ACTION_LOCK = threading.RLock()

def format_duration(delta):
    """Exact duration, e.g. '7d', '4h 12m'. Used for configured values and button labels."""
    total_seconds = max(int(delta.total_seconds()), 0)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f'{days}d')
    if hours:
        parts.append(f'{hours}h')
    if minutes or not parts:
        parts.append(f'{minutes}m')
    return ' '.join(parts)


def format_remaining(delta):
    """Coarsest useful unit for a countdown, e.g. '6d', '5h', '12m'.

    A silence row reading '6d 23h 41m left' is worse than '6d left': the point of the
    countdown is whether it lapses today or next week, not the seconds.
    """
    total_seconds = max(int(delta.total_seconds()), 0)
    if total_seconds >= 86400:
        return f'{total_seconds // 86400}d'
    if total_seconds >= 3600:
        return f'{total_seconds // 3600}h'
    return f'{max(total_seconds // 60, 1)}m'


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
            return 'Alertmanager maintenance is already active.'
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


def get_alertmanager_window_text(state=None):
    """One line describing the maintenance window, or '' when none is active.

    Empty means inactive.  The alerts menu keys its Start/End Maintenance button off
    that emptiness rather than reading the state file itself, because deciding it here
    is what lets this clear state whose silence has already expired -- otherwise a
    stale file would offer End Maintenance for a window that ended hours ago.
    """
    with ALERTMANAGER_ACTION_LOCK:
        return _get_alertmanager_window_text(state)


def _get_alertmanager_window_text(state=None):
    active_state = state or load_alertmanager_mw_state()
    if not active_state or not cfg.ALERTMANAGER_URL:
        return ''

    expires_at = datetime.fromisoformat(active_state['expires_at'])
    remaining = expires_at - datetime.now(ZoneInfo(cfg.TZ))
    if remaining.total_seconds() <= 0:
        clear_alertmanager_mw_state()
        return ''

    from modules.alertmanager import get_silence

    silence = get_silence(active_state.get('silence_id'))
    if not silence:
        # Reported as active, not cleared: the silence may well be in place and dropping
        # it here would leave Alertmanager muted with no button left to unmute it.
        return (
            '🔕 Maintenance active (unverified) — safety expiry '
            f"{expires_at.strftime('%Y-%m-%d %H:%M %Z')}"
        )

    if silence.get('status', {}).get('state') == 'expired':
        clear_alertmanager_mw_state()
        return ''

    return (
        f'🔕 Maintenance active — {format_duration(remaining)} left '
        f"(expires {expires_at.strftime('%H:%M %Z')})"
    )
