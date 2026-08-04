import re
from urllib.parse import urlparse

import requests

import cfg
from modules.common import request_json

SESSION_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')


def _api_origin():
    parsed = urlparse(cfg.ACCESS_CHECK_API_URL or '')
    if (
        parsed.scheme != 'https'
        or not parsed.netloc
        or parsed.path not in ('', '/')
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f'{parsed.scheme}://{parsed.netloc}'


def network_check_is_configured():
    return bool(_api_origin() and cfg.ACCESS_CHECK_API_TOKEN)


def _api_url(path):
    return f'{_api_origin()}{path}'


def _headers():
    return {'Authorization': f'Bearer {cfg.ACCESS_CHECK_API_TOKEN}'}


def create_network_check():
    if not network_check_is_configured():
        return None, 'Automatic network detection is not configured.'

    try:
        payload = request_json('POST', _api_url('/api/sessions'), headers=_headers()) or {}
    except requests.exceptions.RequestException:
        return None, 'Unable to start automatic network detection. Try again later.'

    if not isinstance(payload, dict):
        return None, 'The network detection service returned an invalid session.'
    session_id = str(payload.get('id') or '')
    check_url = str(payload.get('check_url') or '')
    parsed_url = urlparse(check_url)
    expected_path = f'/check/{session_id}'
    if (
        not SESSION_ID_PATTERN.fullmatch(session_id)
        or f'{parsed_url.scheme}://{parsed_url.netloc}' != _api_origin()
        or parsed_url.path != expected_path
        or parsed_url.params
        or parsed_url.query
        or parsed_url.fragment
    ):
        return None, 'The network detection service returned an invalid session.'

    return {'id': session_id, 'check_url': check_url}, None


def get_network_check(session_id):
    try:
        payload = request_json('GET', _api_url(f'/api/sessions/{session_id}'), headers=_headers()) or {}
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None, 'The network detection session expired. Start again.'
        return None, 'Unable to retrieve the detected network. Try again.'
    except requests.exceptions.RequestException:
        return None, 'Unable to retrieve the detected network. Try again.'

    if not isinstance(payload, dict):
        return None, 'The network detection service returned an invalid status.'
    status = payload.get('status')
    if status == 'pending':
        return {'status': 'pending'}, None
    if status != 'complete':
        return None, 'The network detection service returned an invalid status.'

    asn = str(payload.get('asn') or '')
    if not asn.isdigit() or not 0 < int(asn) <= 4_294_967_295:
        return None, 'The network detection service returned an invalid ASN.'

    return {'status': 'complete', 'asn': asn}, None


def delete_network_check(session_id):
    try:
        request_json('DELETE', _api_url(f'/api/sessions/{session_id}'), headers=_headers())
        return True
    except requests.exceptions.RequestException:
        return False
