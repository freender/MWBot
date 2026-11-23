import os
import json
from datetime import timedelta


def _require_env(name):
    value = os.getenv(name)
    if value is None or value == '':
        raise RuntimeError(f'Missing required environment variable: {name}')
    return value


def _get_optional(name, default=None):
    value = os.getenv(name)
    if value is None or value == '':
        return default
    return value


def _get_optional_int(name):
    value = os.getenv(name)
    if value is None or value == '':
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f'Environment variable {name} must be an integer when provided.') from exc


def _get_json(name):
    value = _require_env(name)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Environment variable {name} must contain valid JSON.') from exc


_DURATION_UNITS = {'m': 'minutes', 'h': 'hours', 'd': 'days'}


def _parse_duration(name, value):
    value = str(value).strip().lower()
    amount, unit = value[:-1], value[-1:]
    if not amount.isdigit() or unit not in _DURATION_UNITS or int(amount) <= 0:
        raise RuntimeError(
            f'Environment variable {name} must use a duration like 30m, 12h or 7d.'
        )
    return timedelta(**{_DURATION_UNITS[unit]: int(amount)})


def _get_duration(name, default):
    return _parse_duration(name, _get_optional(name, default))


def _get_durations(name, default):
    """A comma-separated duration list, e.g. `1d,3d,7d`.

    Order is preserved: it is the order the buttons appear in.
    """
    durations = [
        _parse_duration(name, value)
        for value in _get_optional(name, default).split(',')
        if value.strip()
    ]
    if not durations:
        raise RuntimeError(f'Environment variable {name} must list at least one duration.')
    return durations


TOKEN = _require_env('TOKEN')
CHAT_ID = _require_env('CHAT_ID')
WAF_TOKEN = _require_env('WAF_TOKEN')
WAF_ZONE = _require_env('WAF_ZONE')
WAF_RULESET = _require_env('WAF_RULESET')
WAF_RULEID = _require_env('WAF_RULEID')
CDN_URL = _require_env('CDN_URL')
MW_BOT_ASN_DEFAULT = _require_env('MW_BOT_ASN_DEFAULT')
ACCESS_CHECK_API_URL = _get_optional('ACCESS_CHECK_API_URL')
ACCESS_CHECK_API_TOKEN = _get_optional('ACCESS_CHECK_API_TOKEN')
TZ = _require_env('TZ')
SEERR_BASE_URL = _require_env('SEERR_BASE_URL')
SEERR_PUBLIC_URL = os.getenv('SEERR_PUBLIC_URL', '')
SEERR_API_KEY = _require_env('SEERR_API_KEY')
SEERR_ACCESS_TEST_USER_ID = _get_optional_int('SEERR_ACCESS_TEST_USER_ID')
SEERR_ACCESS_TEST_MODE = os.getenv('SEERR_ACCESS_TEST_MODE', '').strip().lower()
SONARR_BASE_URL = _require_env('SONARR_BASE_URL')
SONARR_API_KEY = _require_env('SONARR_API_KEY')
RADARR_BASE_URL = _require_env('RADARR_BASE_URL')
RADARR_API_KEY = _require_env('RADARR_API_KEY')
SONARR4K_BASE_URL = os.getenv('SONARR4K_BASE_URL', SONARR_BASE_URL)
SONARR4K_API_KEY = os.getenv('SONARR4K_API_KEY', SONARR_API_KEY)
RADARR4K_BASE_URL = os.getenv('RADARR4K_BASE_URL', RADARR_BASE_URL)
RADARR4K_API_KEY = os.getenv('RADARR4K_API_KEY', RADARR_API_KEY)

# Alertmanager maintenance
# ALERTMANAGER_URL: base URL of the Alertmanager API, e.g. http://alertmanager:9093
ALERTMANAGER_URL = _get_optional('ALERTMANAGER_URL')
ALERTMANAGER_MW_MATCHERS = _get_json('ALERTMANAGER_MW_MATCHERS') if os.getenv('ALERTMANAGER_MW_MATCHERS') else [
    {'name': 'alertname', 'value': '.+', 'isRegex': True, 'isEqual': True},
]
ALERTMANAGER_OPEN_MW_DURATION = _get_duration('ALERTMANAGER_OPEN_MW_DURATION', '12h')
# Choices offered when silencing one alert, in button order. These are for a fault that is
# already known and tracked -- typically filed as an incident -- where the notification has
# stopped carrying information: a degraded pool waiting on a disk re-alerts daily for a week
# and nobody learns anything on day three.
#
# One day is the floor on purpose. Sub-day silences do not survive a nightly re-alert, so
# they only defer the same interruption to tomorrow. The ceiling is deliberately a week: a
# silence that outlives the attention that created it is how a fault gets forgotten, and
# nothing here re-notifies when one expires.
ALERTMANAGER_ALERT_SILENCE_DURATIONS = _get_durations(
    'ALERTMANAGER_ALERT_SILENCE_DURATIONS', '1d,3d,7d')
# Alerts that represent one-shot events rather than a live condition, identified by
# their `source` label: the PVE and PBS notification webhooks, plus `probe`, a source that
# re-posts a finding at most once a day -- resolving one clears it until the next check.
# Only these can be dismissed by hand: a metric-based alert would simply be re-sent by
# vmalert on its next evaluation.
ALERTMANAGER_RESOLVABLE_SOURCES = [
    value.strip()
    for value in os.getenv('ALERTMANAGER_RESOLVABLE_SOURCES', 'pve,pbs,probe').split(',')
    if value.strip()
]
# Alerts that fire permanently by design and never describe a real condition. `Watchdog`
# is a dead-man's switch: Alertmanager routes it to an external healthcheck, and its
# absence -- not its presence -- is the failure signal. Listing it here would misreport a
# healthy pipeline as an outage and let it be filed as an incident. Excluding it from the
# bot cannot weaken the switch, which is enforced by the external check, not by MWBot.
# Comma-separated; set empty to disable the filter.
ALERTMANAGER_EXCLUDED_ALERTNAMES = {
    value.strip()
    for value in os.getenv('ALERTMANAGER_EXCLUDED_ALERTNAMES', 'Watchdog').split(',')
    if value.strip()
}

# GitHub incident creation
GITHUB_INCIDENT_REPO = _get_optional('GITHUB_INCIDENT_REPO', 'freender/homelab-ops')
GITHUB_INCIDENT_TOKEN = _get_optional('GITHUB_INCIDENT_TOKEN')

# Incident triage runs as a session on an OpenCode server, which the owner then opens in
# OpenChamber and takes over interactively. All four of URL, password, directory and
# public link base are deployment facts and have no defaults; triage is off until set.
OPENCODE_URL = _get_optional('OPENCODE_URL')
OPENCODE_USERNAME = _get_optional('OPENCODE_USERNAME', 'opencode')
OPENCODE_PASSWORD = _get_optional('OPENCODE_PASSWORD')
# The directory the session is created in; it decides which project agents load.
OPENCODE_INCIDENT_DIRECTORY = _get_optional('OPENCODE_INCIDENT_DIRECTORY')
OPENCODE_INCIDENT_AGENT = _get_optional('OPENCODE_INCIDENT_AGENT', 'incident-triage')
# Browser-facing OpenChamber base URL for the Telegram and issue links.
OPENCHAMBER_PUBLIC_URL = _get_optional('OPENCHAMBER_PUBLIC_URL')

# How often to check whether a triage session has finished and posted its verdict. Zero
# disables the watcher; the session link is still sent when the incident is filed.
OPENCODE_TRIAGE_WATCH_SECONDS = int(_get_optional('OPENCODE_TRIAGE_WATCH_SECONDS', '60') or 0)
# A session that has neither finished with a verdict nor been taken over by then is
# reported as having produced no report and is no longer watched.
OPENCODE_TRIAGE_TIMEOUT_SECONDS = int(_get_optional('OPENCODE_TRIAGE_TIMEOUT_SECONDS', '2700') or 0)
