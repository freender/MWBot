import logging
from datetime import datetime, timedelta, timezone

import cfg
from modules.common import normalize_base_url, request_json


_SILENCE_COMMENT = 'mwbot-maintenance'
_SILENCE_CREATED_BY = 'mwbot'
_ALERT_STATUS_LIMIT = 10
_SEVERITY_ORDER = {'critical': 0, 'warning': 1, 'info': 2}


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


def get_active_alerts():
    """Fetch all active alerts, including alerts suppressed by maintenance."""
    try:
        alerts = request_json(
            'GET',
            f'{_base_url()}/api/v2/alerts',
            params={
                'active': 'true',
                'silenced': 'true',
                'inhibited': 'true',
                'unprocessed': 'true',
            },
            timeout=10,
        )
        if not isinstance(alerts, list):
            logging.error('Alertmanager alerts API returned an invalid payload')
            return None
        return alerts
    except Exception as exc:
        logging.error('Failed to fetch active Alertmanager alerts: %s', exc)
        return None


def _alert_target(labels):
    name = labels.get('name')
    host = labels.get('host')
    if name and host and name != host:
        return f'{name} @ {host}'
    return name or host or labels.get('instance') or labels.get('job') or 'unknown target'


def _alert_annotation(alert, *names):
    annotations = alert.get('annotations') or {}
    for name in names:
        value = (annotations.get(name) or '').strip()
        if value:
            return value
    return ''


def alert_button_label(alert, limit=48):
    """Short one-line label for an inline keyboard button."""
    labels = alert.get('labels') or {}
    severity = (labels.get('severity') or '').lower()
    icon = {'critical': '🔴', 'warning': '🟡', 'info': '🔵'}.get(severity, '⚪')
    text = f'{icon} {_alert_target(labels)}: {labels.get("alertname", "UnknownAlert")}'
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + '…'
    return text


def build_alert_incident_text(alert):
    """Render an Alertmanager alert as incident report text.

    The first line becomes the GitHub issue title, so it stays short and specific.
    """
    labels = alert.get('labels') or {}
    alert_name = labels.get('alertname', 'UnknownAlert')
    target = _alert_target(labels)
    lines = [f'{alert_name} on {target}']

    description = _alert_annotation(alert, 'description', 'summary', 'message')
    if description:
        lines.extend(['', description])

    details = []
    for key in ('severity', 'host', 'name', 'instance', 'job'):
        value = (labels.get(key) or '').strip()
        if value:
            details.append(f'- {key}: {value}')
    started = (alert.get('startsAt') or '').strip()
    if started:
        details.append(f'- firing since: {started}')
    status = alert.get('status') or {}
    markers = []
    if status.get('silencedBy'):
        markers.append('silenced')
    if status.get('inhibitedBy'):
        markers.append('inhibited')
    if markers:
        details.append(f'- suppressed: {", ".join(markers)}')
    if details:
        lines.extend(['', 'Alert labels:', *details])

    runbook = _alert_annotation(alert, 'runbook_url', 'runbook')
    if runbook:
        lines.extend(['', f'Runbook: {runbook}'])
    return '\n'.join(lines)


def get_incident_alert_choices(limit=_ALERT_STATUS_LIMIT):
    """Active alerts sorted for incident selection.

    Returns None when Alertmanager is unreachable, so callers can distinguish
    "nothing is firing" from "we could not ask".
    """
    if not cfg.ALERTMANAGER_URL:
        return None
    alerts = get_active_alerts()
    if alerts is None:
        return None
    return sorted(alerts, key=_alert_sort_key)[:limit]


def _alert_sort_key(alert):
    labels = alert.get('labels') or {}
    severity = labels.get('severity', '').lower()
    return (
        _SEVERITY_ORDER.get(severity, len(_SEVERITY_ORDER)),
        _alert_target(labels).lower(),
        labels.get('alertname', '').lower(),
    )


def format_alert_status(alerts, limit=_ALERT_STATUS_LIMIT):
    if alerts is None:
        return 'DOWN: Alertmanager API unavailable.\nAlerts could not be loaded.'
    if not alerts:
        return (
            'UP: Alertmanager API\n'
            'DOWN: None\n'
            'All monitored alert conditions are clear.'
        )

    sorted_alerts = sorted(alerts, key=_alert_sort_key)
    suppressed_count = sum(
        bool((alert.get('status') or {}).get('silencedBy') or
             (alert.get('status') or {}).get('inhibitedBy'))
        for alert in sorted_alerts
    )
    summary = f'DOWN: {len(sorted_alerts)} active alert'
    if len(sorted_alerts) != 1:
        summary += 's'
    if suppressed_count:
        summary += f' ({suppressed_count} suppressed)'

    lines = ['UP: Alertmanager API', summary]
    for alert in sorted_alerts[:limit]:
        labels = alert.get('labels') or {}
        severity = labels.get('severity', 'unknown').upper()
        alert_name = labels.get('alertname', 'UnknownAlert')
        status = alert.get('status') or {}
        markers = []
        if status.get('silencedBy'):
            markers.append('silenced')
        if status.get('inhibitedBy'):
            markers.append('inhibited')
        suffix = f" [{', '.join(markers)}]" if markers else ''
        lines.append(f'- {severity} {_alert_target(labels)}: {alert_name}{suffix}')

    remaining = len(sorted_alerts) - limit
    if remaining > 0:
        lines.append(f'- ...and {remaining} more')
    return '\n'.join(lines)


def get_alertmanager_alert_status_text():
    return format_alert_status(get_active_alerts())


def extend_silence(silence_id, new_duration, matchers=None, comment=None):
    """Expire the existing silence and create a new one with a fresh duration.

    Returns the new silence ID or None on failure.
    """
    expire_silence(silence_id)
    return create_silence(new_duration, matchers=matchers, comment=comment)
