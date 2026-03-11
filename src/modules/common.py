import requests


def normalize_base_url(url):
    return url.rstrip('/')


def build_api_headers(api_key):
    return {
        'X-Api-Key': api_key,
        'Content-Type': 'application/json',
    }


def request_json(method, url, headers=None, params=None, payload=None, timeout=30):
    response = requests.request(
        method,
        url,
        headers=headers,
        params=params,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    if not response.content:
        return None
    return response.json()


def extract_records(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get('records', [])
    return []

