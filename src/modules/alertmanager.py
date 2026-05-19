import logging
from datetime import datetime, timedelta, timezone

import cfg
from modules.common import normalize_base_url, request_json


_SILENCE_COMMENT = 'mwbot-maintenance'
_SILENCE_CREATED_BY = 'mwbot'


def _base_url():
    return normalize_base_url(cfg.ALERTMANAGER_URL)


def _silence_payload(duration, matchers=None, comment=None):
    """Build a POST /api/v2/silences body."""
    now = datetime.now(timezone.utc)
    ends_at = now + duration
    used_matchers = matchers or cfg.ALERTMANAGER_MW_MATCHERS
    return {
        'matchers': used_matchers,
        'startsAt': now.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'endsAt': ends_at.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'createdBy': _SILENCE_CREATED_BY,
        'comment': comment or _SILENCE_COMMENT,
    }


def create_silence(duration, matchers=None, comment=None):
    """Create an Alertmanager silence.  Returns the silence ID or None on failure."""
    payload = _silence_payload(duration, matchers=matchers, comment=comment)
    try:
        result = request_json('POST', f'{_base_url()}/api/v2/silences', payload=payload, timeout=10)
        silence_id = (result or {}).get('silenceID')
        if silence_id:
            logging.info('Alertmanager silence created: %s (duration=%s)', silence_id, duration)
        else:
            logging.error('Alertmanager silence POST returned no silenceID: %s', result)
        return silence_id
    except Exception as exc:
        logging.error('Failed to create Alertmanager silence: %s', exc)
        return None


def expire_silence(silence_id):
    """Delete (expire) an Alertmanager silence by ID.  Returns True on success."""
    if not silence_id:
        return False
    try:
        request_json('DELETE', f'{_base_url()}/api/v2/silence/{silence_id}', timeout=10)
        logging.info('Alertmanager silence expired: %s', silence_id)
        return True
    except Exception as exc:
        logging.error('Failed to expire Alertmanager silence %s: %s', silence_id, exc)
        return False


def get_silence(silence_id):
    """Fetch a silence by ID.  Returns the silence dict or None."""
    if not silence_id:
        return None
    try:
        return request_json('GET', f'{_base_url()}/api/v2/silence/{silence_id}', timeout=10)
    except Exception as exc:
        logging.error('Failed to fetch Alertmanager silence %s: %s', silence_id, exc)
        return None


def extend_silence(silence_id, new_duration, matchers=None, comment=None):
    """Expire the existing silence and create a new one with a fresh duration.

    Returns the new silence ID or None on failure.
    """
    expire_silence(silence_id)
    return create_silence(new_duration, matchers=matchers, comment=comment)
