import os
import json


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


TOKEN = _require_env('TOKEN')
CHAT_ID = _require_env('CHAT_ID')
NOTIFY_CHAT_ID = os.getenv('NOTIFY_CHAT_ID', CHAT_ID)
OWNER = _get_int('OWNER')
KUMA_HOST = _require_env('KUMA_HOST')
KUMA_LOGIN = _require_env('KUMA_LOGIN')
KUMA_PASSWORD = _require_env('KUMA_PASSWORD')
KUMA_MW_ID = _get_int('KUMA_MW_ID')
WAF_TOKEN = _require_env('WAF_TOKEN')
WAF_ZONE = _require_env('WAF_ZONE')
WAF_RULESET = _require_env('WAF_RULESET')
WAF_RULEID = _require_env('WAF_RULEID')
CDN_URL = _require_env('CDN_URL')
TELEGRAM_AUTH_USERS = _get_json('TELEGRAM_AUTH_USERS')
MW_BOT_ASN_DEFAULT = _require_env('MW_BOT_ASN_DEFAULT')
TZ = _require_env('TZ')
SEERR_BASE_URL = _require_env('SEERR_BASE_URL')
SEERR_PUBLIC_URL = os.getenv('SEERR_PUBLIC_URL', '')
SEERR_API_KEY = _require_env('SEERR_API_KEY')
SEERR_ACCESS_ENV_ONLY = _get_bool('SEERR_ACCESS_ENV_ONLY', default=False)
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

# ── Alertmanager silencing ─────────────────────────────────────────────────────
# ALERTMANAGER_URL: base URL of the Alertmanager API, e.g. http://alertmanager:9093
# When set, maintenance windows also create/expire Alertmanager silences.
# MAINTENANCE_BACKENDS: comma-separated list of active backends.
#   "kuma"          - Uptime Kuma only (default when ALERTMANAGER_URL not set)
#   "alertmanager"  - Alertmanager silence only
#   "kuma,alertmanager" (or "alertmanager,kuma") - dual-write
ALERTMANAGER_URL = _get_optional('ALERTMANAGER_URL')
ALERTMANAGER_MW_MATCHERS = _get_json('ALERTMANAGER_MW_MATCHERS') if os.getenv('ALERTMANAGER_MW_MATCHERS') else [
    {'name': 'alertname', 'value': '.*', 'isRegex': True, 'isEqual': True},
]
_raw_backends = _get_optional('MAINTENANCE_BACKENDS', 'kuma')
MAINTENANCE_BACKENDS = {b.strip().lower() for b in _raw_backends.split(',') if b.strip()}
