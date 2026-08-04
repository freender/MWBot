import ipaddress
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import cfg
from modules.common import request_json

_WAF_UPDATE_LOCK = threading.Lock()


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


def _build_rule_payload(ip_addresses, asns, enabled):
    source_expressions = []
    if ip_addresses:
        expression_ips = ' '.join(map(str, ip_addresses))
        source_expressions.append(f'ip.src in {{{expression_ips}}}')
    if asns:
        expression_asns = ' '.join(map(str, asns))
        source_expressions.append(f'ip.geoip.asnum in {{{expression_asns}}}')
    # ASN-wide access is intentional; the exact IP covers lookup/Cloudflare ASN mismatches.
    source_expression = ' or '.join(source_expressions)
    return {
        'action': 'skip',
        'action_parameters': {'ruleset': 'current'},
        'expression': f'(({source_expression}) and http.host wildcard "{cfg.CDN_URL}")',
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


def get_networks_from_firewall_rule():
    rule, error = _get_waf_rule()
    if rule is None:
        return None, None, error

    expression = rule.get('expression', '')
    ip_match = re.search(r'ip\.src\s+in\s+\{([^}]*)\}', expression)
    asn_match = re.search(r'ip\.geoip\.asnum\s+in\s+\{([^}]*)\}', expression)
    ip_addresses = []
    if ip_match:
        for segment in ip_match.group(1).split():
            try:
                ip_addresses.append(str(ipaddress.ip_address(segment)))
            except ValueError:
                continue
    asns = []
    if asn_match:
        asns = [segment for segment in asn_match.group(1).split() if segment.isdigit()]
    logging.info('Old Rule: %s IPs, %s ASNs', len(ip_addresses), len(asns))
    return ip_addresses, asns, None


def grant_network_access(ip_address, asn):
    try:
        normalized_ip = str(ipaddress.ip_address(ip_address))
    except (TypeError, ValueError):
        return False, 'Unable to add access because the detected IP address is invalid.'
    normalized_asn = str(asn)
    if not normalized_asn.isdigit() or not 0 < int(normalized_asn) <= 4_294_967_295:
        return False, 'Unable to add access because the detected ASN is invalid.'

    with _WAF_UPDATE_LOCK:
        old_ips, old_asns, error = get_networks_from_firewall_rule()
        if old_ips is None:
            result = f'An error occurred while retrieving network access: {error}'
            logging.error(result)
            return False, result

        changed = False
        if normalized_ip not in old_ips:
            old_ips.append(normalized_ip)
            changed = True
        if normalized_asn not in old_asns:
            old_asns.append(normalized_asn)
            changed = True
        if not changed:
            result = 'This network already has access.'
            logging.info(result)
            return True, result

        logging.info('New Rule: %s IPs, %s ASNs', len(old_ips), len(old_asns))
        success, error = _update_firewall_rule(_build_rule_payload(old_ips, old_asns, enabled=True))
        if success:
            result = 'Your current IP and ISP have been granted temporary access.'
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

    ip_addresses, asns, error = get_networks_from_firewall_rule()
    if ip_addresses is None:
        result = f'Unable to retrieve Plex access details: {error}'
        logging.error(result)
        return result

    temporary_asns = [asn for asn in asns if str(asn) != str(cfg.MW_BOT_ASN_DEFAULT)]
    if not ip_addresses and not temporary_asns:
        return 'Plex access is enabled.'

    details = []
    if ip_addresses:
        ip_label = 'IP address' if len(ip_addresses) == 1 else 'IP addresses'
        details.append(f'{len(ip_addresses)} temporary {ip_label}')
    if temporary_asns:
        details.append(f'ASNs: {", ".join(temporary_asns)}')
    return f'Plex access is enabled. {"; ".join(details)}.'


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
    rule_data = _build_rule_payload([], [cfg.MW_BOT_ASN_DEFAULT], enabled=False)
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
