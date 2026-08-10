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


def _get_int(name):
    value = _require_env(name)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f'Environment variable {name} must be an integer.') from exc


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


def _get_bool(name, default=False):
    value = os.getenv(name)
    if value is None or value == '':
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _get_duration(name, default):
    value = _get_optional(name, default).strip().lower()
    if len(value) < 2:
        raise RuntimeError(f'Environment variable {name} must use a duration like 30m or 12h.')
    amount = value[:-1]
    unit = value[-1]
    if not amount.isdigit() or unit not in ('m', 'h'):
        raise RuntimeError(f'Environment variable {name} must use a duration like 30m or 12h.')
    if unit == 'm':
        return timedelta(minutes=int(amount))
    return timedelta(hours=int(amount))


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
# Alerts that represent one-shot events rather than a live condition, identified by
# their `source` label. Only these can be dismissed by hand: a metric-based alert
# would simply be re-sent by vmalert on its next evaluation.
ALERTMANAGER_RESOLVABLE_SOURCES = [
    value.strip()
    for value in os.getenv('ALERTMANAGER_RESOLVABLE_SOURCES', 'pve').split(',')
    if value.strip()
]

# GitHub incident creation
GITHUB_INCIDENT_REPO = _get_optional('GITHUB_INCIDENT_REPO', 'freender/homelab-ops')
GITHUB_INCIDENT_TOKEN = _get_optional('GITHUB_INCIDENT_TOKEN')
