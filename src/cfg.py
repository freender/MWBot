import os
import json


def _require_env(name):
    value = os.getenv(name)
    if value is None or value == '':
        raise RuntimeError(f'Missing required environment variable: {name}')
    return value


def _get_int(name):
    value = _require_env(name)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f'Environment variable {name} must be an integer.') from exc


def _get_json(name):
    value = _require_env(name)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Environment variable {name} must contain valid JSON.') from exc


TOKEN = _require_env('TOKEN')
TOKEN_STAGING = _require_env('TOKEN_STAGING')
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
SEERR_API_KEY = _require_env('SEERR_API_KEY')
SONARR_BASE_URL = _require_env('SONARR_BASE_URL')
SONARR_API_KEY = _require_env('SONARR_API_KEY')
RADARR_BASE_URL = _require_env('RADARR_BASE_URL')
RADARR_API_KEY = _require_env('RADARR_API_KEY')
SONARR4K_BASE_URL = os.getenv('SONARR4K_BASE_URL', SONARR_BASE_URL)
SONARR4K_API_KEY = os.getenv('SONARR4K_API_KEY', SONARR_API_KEY)
RADARR4K_BASE_URL = os.getenv('RADARR4K_BASE_URL', RADARR_BASE_URL)
RADARR4K_API_KEY = os.getenv('RADARR4K_API_KEY', RADARR_API_KEY)
