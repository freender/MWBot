import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import cfg
from modules.common import request_json

_WAF_UPDATE_LOCK = threading.Lock()
_AS_ORGANIZATIONS_LOCK = threading.Lock()
AS_ORGANIZATIONS_FILE = '/config/as_organizations.json'


def convert_to_local_time(timestamp):
    utc_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    server_timezone = ZoneInfo(cfg.TZ)
    return utc_time.astimezone(server_timezone)


def _cloudflare_headers():
    return {'Authorization': f'Bearer {cfg.WAF_TOKEN}'}


def _ruleset_url():
    return f'https://api.cloudflare.com/client/v4/zones/{cfg.WAF_ZONE}/rulesets/{cfg.WAF_RULESET}'


def _rule_url():
    return f'{_ruleset_url()}/rules/{cfg.WAF_RULEID}'


def _get_waf_rule():
    try:
        payload = request_json('GET', _ruleset_url(), headers=_cloudflare_headers()) or {}
        rules = payload.get('result', {}).get('rules', [])
        for rule in rules:
            if rule.get('id') == cfg.WAF_RULEID:
                return rule, None

        result = 'Unable to locate the configured WAF rule.'
        logging.error(result)
        return None, result
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        result = f'Failed to retrieve the rule. Status code: {status_code}'
        logging.error('%s: %s', result, exc)
        return None, result
    except requests.exceptions.RequestException as exc:
        result = f'Unexpected error occurred: {exc}'
        logging.error(result)
        return None, result


def _build_rule_payload(asns, enabled):
    expression_asns = ' '.join(map(str, asns))
    return {
        'action': 'skip',
        'action_parameters': {'ruleset': 'current'},
        'expression': f'(ip.geoip.asnum in {{{expression_asns}}} and http.host wildcard "{cfg.CDN_URL}")',
        'description': 'Whitelist MWBot',
        'enabled': enabled,
    }


def _update_firewall_rule(rule_data):
    try:
        request_json('PATCH', _rule_url(), headers=_cloudflare_headers(), payload=rule_data)
        return True, None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        result = f'Failed to update rule. Status code: {status_code}'
        logging.error('%s: %s', result, exc)
        return False, result
    except requests.exceptions.RequestException as exc:
        result = f'Unexpected error occurred: {exc}'
        logging.error(result)
        return False, result


def _load_as_organizations():
    with _AS_ORGANIZATIONS_LOCK:
        if not os.path.exists(AS_ORGANIZATIONS_FILE):
            return {}
        try:
            with open(AS_ORGANIZATIONS_FILE, 'r', encoding='utf-8') as handle:
                organizations = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning('Unable to read ASN organization cache: %s', exc)
            return {}
    if not isinstance(organizations, dict):
        return {}
    return {
        str(asn): organization
        for asn, organization in organizations.items()
        if str(asn).isdigit() and isinstance(organization, str) and organization
    }


def _save_as_organization(asn, organization):
    if not isinstance(organization, str):
        return
    organization = organization.strip()
    if not organization or len(organization) > 120:
        return

    with _AS_ORGANIZATIONS_LOCK:
        organizations = _load_as_organizations_unlocked()
        organizations[str(asn)] = organization
        try:
            os.makedirs(os.path.dirname(AS_ORGANIZATIONS_FILE), exist_ok=True)
            with open(AS_ORGANIZATIONS_FILE, 'w', encoding='utf-8') as handle:
                json.dump(organizations, handle)
        except OSError as exc:
            logging.warning('Unable to save ASN organization cache: %s', exc)


def _load_as_organizations_unlocked():
    if not os.path.exists(AS_ORGANIZATIONS_FILE):
        return {}
    try:
        with open(AS_ORGANIZATIONS_FILE, 'r', encoding='utf-8') as handle:
            organizations = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning('Unable to read ASN organization cache: %s', exc)
        return {}
    return organizations if isinstance(organizations, dict) else {}


def get_asns_from_firewall_rule():
    rule, error = _get_waf_rule()
    if rule is None:
        return None, error

    expression = rule.get('expression', '')
    asn_match = re.search(r'ip\.geoip\.asnum\s+in\s+\{([^}]*)\}', expression)
    asns = []
    if asn_match:
        asns = [segment for segment in asn_match.group(1).split() if segment.isdigit()]
    logging.info('Old Rule: %s ASNs', len(asns))
    return asns, None


def grant_network_access(asn, as_organization=None):
    normalized_asn = str(asn)
    if not normalized_asn.isdigit() or not 0 < int(normalized_asn) <= 4_294_967_295:
        return False, 'Unable to add access because the detected ASN is invalid.'

    with _WAF_UPDATE_LOCK:
        old_asns, error = get_asns_from_firewall_rule()
        if old_asns is None:
            result = f'An error occurred while retrieving network access: {error}'
            logging.error(result)
            return False, result

        if normalized_asn not in old_asns:
            old_asns.append(normalized_asn)

        logging.info('New Rule: %s ASNs', len(old_asns))
        success, error = _update_firewall_rule(_build_rule_payload(old_asns, enabled=True))
        if success:
            _save_as_organization(normalized_asn, as_organization)
            result = 'Network access granted.'
            logging.info(result)
            return True, result
        return False, error


def get_rule_status():
    rule, error = _get_waf_rule()
    if rule is None:
        return None, error

    enabled = rule.get('enabled')
    if enabled is None:
        result = 'Failed to retrieve the rule enabled state.'
        logging.error(result)
        return None, result
    return enabled, None


def get_firewall_status_text():
    enabled, error = get_rule_status()
    if enabled is None:
        result = f'Unable to retrieve Plex access status: {error}'
        logging.error(result)
        return result

    if not enabled:
        return 'Plex access is disabled.'

    asns, error = get_asns_from_firewall_rule()
    if asns is None:
        result = f'Unable to retrieve Plex access details: {error}'
        logging.error(result)
        return result

    temporary_asns = [asn for asn in asns if str(asn) != str(cfg.MW_BOT_ASN_DEFAULT)]
    if not temporary_asns:
        return 'Plex access is enabled.'

    organizations = _load_as_organizations()
    networks = ', '.join(
        f'{organizations[asn]} (AS{asn})' if asn in organizations else f'AS{asn}'
        for asn in temporary_asns
    )
    return f'Plex access is enabled. Temporary networks: {networks}.'


def get_rule_modify_date():
    rule, error = _get_waf_rule()
    if rule is None:
        return None, error

    modify_date = rule.get('last_updated')
    if modify_date is None:
        result = 'Failed to retrieve the rule modification date.'
        logging.error(result)
        return None, result
    return modify_date, None


def disable_asn_to_firewall_rule():
    rule_data = _build_rule_payload([cfg.MW_BOT_ASN_DEFAULT], enabled=False)
    with _WAF_UPDATE_LOCK:
        success, error = _update_firewall_rule(rule_data)
    if success:
        result = 'Firewall rule has been disabled.'
        logging.info(result)
        return result
    return error


def get_next_firewall_run(current_time):
    next_run = current_time.replace(hour=3, minute=40, second=0, microsecond=0)
    if next_run <= current_time:
        next_run += timedelta(days=1)
    return next_run


def schedule_fw_task(shutdown_event=None):
    while shutdown_event is None or not shutdown_event.is_set():
        current_time = datetime.now(ZoneInfo(cfg.TZ))
        next_run = get_next_firewall_run(current_time)
        delay = (next_run - current_time).total_seconds()

        logging.info('[%s] Next run scheduled at %s (in %s seconds)', current_time, next_run, delay)
        if shutdown_event is not None and shutdown_event.wait(delay):
            break
        if shutdown_event is None:
            time.sleep(delay)

        status, error = get_rule_status()
        if status is None:
            result = f'An error occurred while retrieving the rule status: {error}'
            logging.error(result)
            continue

        if status:
            modify_str, error = get_rule_modify_date()
            if modify_str is None:
                logging.error('An error occurred while retrieving the rule modification date: %s', error)
                continue

            modify_local_time = convert_to_local_time(modify_str)
            current_time = datetime.now(ZoneInfo(cfg.TZ))
            if modify_local_time + timedelta(days=7) < current_time:
                disable_asn_to_firewall_rule()
