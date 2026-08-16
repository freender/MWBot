import logging
from datetime import datetime, timezone

import cfg
from modules.common import normalize_base_url, request_json


_SILENCE_COMMENT = 'mwbot-maintenance'
_SILENCE_CREATED_BY = 'mwbot'
# Marks a silence created for one specific alert rather than the blanket maintenance
# window.  It is how Unsilence tells a silence it may expire apart from one it may not:
# see `alert_silence_ids`.
ALERT_SILENCE_COMMENT = 'mwbot-alert-silence'
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


def _is_excluded(alert):
    """True for alerts that fire by design and never describe a real condition.

    See `ALERTMANAGER_EXCLUDED_ALERTNAMES` in `cfg.py`.
    """
    alertname = ((alert or {}).get('labels') or {}).get('alertname', '').strip()
    return alertname in cfg.ALERTMANAGER_EXCLUDED_ALERTNAMES


def get_active_alerts():
    """Fetch active alerts, including ones suppressed by maintenance.

    Filtering here rather than at each call site keeps the status list, the incident
    picker and the resolve picker consistent: an alert we refuse to report is also one
    we refuse to file.  Returns None on failure so callers can still tell "nothing is
    firing" apart from "we could not ask".
    """
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
        return [alert for alert in alerts if not _is_excluded(alert)]
    except Exception as exc:
        logging.error('Failed to fetch active Alertmanager alerts: %s', exc)
        return None


def is_resolvable(alert):
    """True when an alert is a one-shot event that will never clear itself.

    Proxmox posts discrete events (a failed backup, a failed replication) straight
    to the Alertmanager API.  Nothing re-evaluates them, so they linger until
    `resolve_timeout` expires.  Metric-based alerts are excluded because vmalert
    re-sends them within one evaluation interval, which would make the button look
    broken rather than declining the request.
    """
    source = ((alert.get('labels') or {}).get('source') or '').strip()
    return bool(source) and source in cfg.ALERTMANAGER_RESOLVABLE_SOURCES


def get_alert_choices(limit=_ALERT_STATUS_LIMIT):
    """Active alerts, sorted for display as an inline keyboard.

    One list for the whole alerts section.  Filing, resolving and silencing used to
    each fetch their own, which meant the same alert could be offered by one picker
    and missing from another.  Per-action eligibility is decided per alert at render
    time (`is_resolvable`, `alert_silence_ids`), not by filtering the list.

    Returns None when Alertmanager is unreachable, so callers can tell "nothing is
    firing" apart from "we could not ask".
    """
    if not cfg.ALERTMANAGER_URL:
        return None
    alerts = get_active_alerts()
    if alerts is None:
        return None
    return sorted(alerts, key=_alert_sort_key)[:limit]


def alert_silence_matchers(alert):
    """Exact matchers for one alert's label set.

    Every label is matched exactly, so the silence covers this alert and nothing
    adjacent to it.  If a label changes the silence stops applying -- correctly, since
    a different label set is a different alert with a different fingerprint.
    """
    labels = (alert or {}).get('labels') or {}
    return [
        {'name': name, 'value': value, 'isRegex': False, 'isEqual': True}
        for name, value in sorted(labels.items())
    ]


def silence_alert(alert, duration):
    """Silence a single alert.  Returns the silence ID or None on failure."""
    matchers = alert_silence_matchers(alert)
    if not matchers:
        logging.error('Refusing to silence an alert with no labels')
        return None
    return create_silence(duration, matchers=matchers, comment=ALERT_SILENCE_COMMENT)


def get_silences():
    """Every silence Alertmanager knows about.  [] on failure."""
    try:
        result = request_json('GET', f'{_base_url()}/api/v2/silences', timeout=10)
        return result if isinstance(result, list) else []
    except Exception as exc:
        logging.error('Failed to fetch Alertmanager silences: %s', exc)
        return []


def silence_index():
    """All silences keyed by ID.

    One call resolves `silencedBy` for a whole alert list.  Rendering the list used to
    cost a GET per silenced alert, which grew with exactly the thing the list is for.
    """
    return {
        silence.get('id'): silence
        for silence in get_silences()
        if isinstance(silence, dict) and silence.get('id')
    }


def alert_silences(alert, index=None):
    """Our own silences suppressing this alert, as `(id, silence)` pairs.

    Deliberately not everything in `silencedBy`: the blanket maintenance window also
    suppresses this alert, and an Unsilence button on one alert must not be able to
    lift the window over all of them.  Only silences we created for this alert -- by
    comment -- are eligible.

    `index` is a `silence_index()` result; pass it when rendering more than one alert.
    """
    silenced_by = ((alert or {}).get('status') or {}).get('silencedBy') or []
    found = []
    for silence_id in silenced_by:
        silence = index.get(silence_id) if index is not None else get_silence(silence_id)
        if (silence or {}).get('comment') == ALERT_SILENCE_COMMENT:
            found.append((silence_id, silence))
    return found


def alert_silence_ids(alert, index=None):
    return [silence_id for silence_id, _ in alert_silences(alert, index=index)]


def _parse_api_time(value):
    """Parse an Alertmanager timestamp.  None if it is missing or unparseable."""
    text = str(value or '').strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def alert_silenced_until(alert, index=None):
    """When our silence on this alert lapses, or None if we are not silencing it.

    The latest of ours, so overlapping silences report the moment it actually goes
    audible again rather than the first one to expire.
    """
    ends = [
        parsed
        for _, silence in alert_silences(alert, index=index)
        if (parsed := _parse_api_time((silence or {}).get('endsAt')))
    ]
    return max(ends) if ends else None


def unsilence_alert(alert):
    """Expire this alert's own silences.  False when there were none, or one failed."""
    silence_ids = alert_silence_ids(alert)
    if not silence_ids:
        return False
    # Materialised so a first failure does not skip the remaining silences.
    results = [expire_silence(silence_id) for silence_id in silence_ids]
    return all(results)


def resolve_alert(alert):
    """Resolve a single alert by re-posting its label set with endsAt in the past.

    Alertmanager has no delete endpoint.  An alert is cleared by sending the same
    label set again with an `endsAt` that has passed, which moves it out of the
    active list immediately.  The labels must match exactly or a second, distinct
    alert is created instead.
    """
    labels = alert.get('labels') or {}
    if not labels:
        logging.error('Refusing to resolve an alert with no labels')
        return False

    now = datetime.now(timezone.utc)
    starts_at = alert.get('startsAt')
    payload = {
        'labels': labels,
        'annotations': alert.get('annotations') or {},
        'endsAt': now.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
    }
    if starts_at:
        payload['startsAt'] = starts_at

    try:
        request_json('POST', f'{_base_url()}/api/v2/alerts', payload=[payload], timeout=10)
    except Exception as exc:
        logging.error('Failed to resolve alert %s: %s', labels.get('alertname'), exc)
        return False

    logging.info(
        'Resolved alert %s on %s',
        labels.get('alertname', 'UnknownAlert'),
        _alert_target(labels),
    )
    return True


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
    markers = suppression_markers(alert)
    if markers:
        details.append(f'- suppressed: {", ".join(markers)}')
    if details:
        lines.extend(['', 'Alert labels:', *details])

    runbook = _alert_annotation(alert, 'runbook_url', 'runbook')
    if runbook:
        lines.extend(['', f'Runbook: {runbook}'])
    return '\n'.join(lines)


def alert_fingerprint(alert):
    """Alertmanager's hash of the alert's label set.

    Stable for as long as the labels are, which is what makes it usable both as a dedup
    key when filing and as the correlation key when the triage repo checks whether the
    alert an incident came from has stopped firing.
    """
    return str((alert or {}).get('fingerprint') or '').strip()


def _alert_sort_key(alert):
    labels = alert.get('labels') or {}
    severity = labels.get('severity', '').lower()
    return (
        _SEVERITY_ORDER.get(severity, len(_SEVERITY_ORDER)),
        _alert_target(labels).lower(),
        labels.get('alertname', '').lower(),
    )


def suppression_markers(alert):
    """Why this alert is not notifying, if it is not: 'silenced', 'inhibited'."""
    status = (alert or {}).get('status') or {}
    markers = []
    if status.get('silencedBy'):
        markers.append('silenced')
    if status.get('inhibitedBy'):
        markers.append('inhibited')
    return markers


def format_alert_summary(alerts):
    """One line above the alert list.

    The list itself carries the per-alert detail that a text status used to spell out,
    so this only has to say how much there is and how much of it is muted.
    """
    if alerts is None:
        return 'Alertmanager is unavailable; alerts could not be loaded.'
    if not alerts:
        return 'All clear. Nothing is firing.'
    suppressed = sum(1 for alert in alerts if suppression_markers(alert))
    text = f'{len(alerts)} active alert' + ('' if len(alerts) == 1 else 's')
    if suppressed:
        text += f' · {suppressed} suppressed'
    return text
