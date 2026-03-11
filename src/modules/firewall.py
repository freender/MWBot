import ipaddress
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import cfg
from modules.common import request_json


def is_valid_ip(ip):
    try:
        ipaddress.ip_address(ip)
        return True
    except (ValueError, TypeError):
        return False


def get_asn_from_ip(ip):
    try:
        url = f'http://ip-api.com/json/{ip}?fields=as'
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        as_field = payload.get('as') or ''
        if not as_field.startswith('AS'):
            return None, 'ASN for this IP is not found!\nDoublecheck and rerun /ip command'
        asn = as_field.removeprefix('AS').split()[0]
        if not asn.isdigit():
            return None, 'ASN for this IP is not found!\nDoublecheck and rerun /ip command'
        return asn, None
    except requests.exceptions.RequestException as exc:
        result = 'ASN for this IP is not found!\nDoublecheck and rerun /ip command'
        logging.error('%s: %s', result, exc)
        return None, result


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


def get_asns_from_firewall_rule():
    rule, error = _get_waf_rule()
    if rule is None:
        return None, error

    expression = rule.get('expression', '')
    asns = [segment for segment in expression.split('{', 1)[-1].split('}', 1)[0].split() if segment.isdigit()]
    logging.info('Old Rule: %s', ' '.join(map(str, asns)))
    return asns, None


def add_asn_to_firewall_rule(asn):
    old_asns, error = get_asns_from_firewall_rule()
    if old_asns is None:
        result = f'An error occurred while retrieving ASNs from the firewall rule: {error}'
        logging.error(result)
        return result

    if asn in old_asns:
        result = f'ASN {asn} already exists in the firewall rule.'
        logging.info(result)
        return result

    old_asns.append(asn)
    logging.info('New Rule: %s', ' '.join(map(str, old_asns)))
    success, error = _update_firewall_rule(_build_rule_payload(old_asns, enabled=True))
    if success:
        result = f'ASN {asn} has been successfully added to the firewall rule.'
        logging.info(result)
        return result
    return error


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


def schedule_fw_task():
    while True:
        current_time = datetime.now(ZoneInfo(cfg.TZ))
        next_run = get_next_firewall_run(current_time)
        delay = (next_run - current_time).total_seconds()

        logging.info('[%s] Next run scheduled at %s (in %s seconds)', current_time, next_run, delay)
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
