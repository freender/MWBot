import re
import json
import requests
import cfg
import logging
import time
import pytz
import socketio.exceptions
from datetime import datetime
from datetime import timedelta
from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api import UptimeKumaException


COMMANDS = {
'start': 'Start Screen',
'ip': 'Allow Plex in unknown place',
'reset_ip': 'Disable Plex in unknown place',
'start_silent': 'Start Silent MW',
'stop_silent': 'Stop Silent MW',
'firmware_mw': 'Start Firmware MW and notify Sev1 chat',
'reboot_mw': 'Start Reboot MW and notify Sev1 chat',
'generic_mw': 'Start MW and notify Sev1 chat',
'stop_mw': 'Stop MW and notify Sev1 chat',
}

def is_command(string):
    pattern = r"^\/.*$"
    return bool(re.match(pattern, string))

def is_owner(message):
    logging.warning(f"Auth attempt for CID: {message.chat.id}")
    return bool(message.chat.id == cfg.OWNER)

def is_auth_user(message):
    logging.warning(f"Auth attempt for CID: {message.chat.id}")
    return bool(str(message.chat.id) in cfg.TELEGRAM_AUTH_USERS)

def is_valid_ip(ip):
    # Regular expression patterns to match valid IPv4 and IPv6 addresses
    ipv4_pattern = r'^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
    ipv6_pattern = r'^([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){1,7}:$|^([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}$|^([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}$|^([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}$|^([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}$|^[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})$|::[fF]{4}(:0{0,4}){0,1}(:0{0,4}:255(\.255){3}|(:0{0,4}){1,2}:255(\.255){2}|((:0{0,4}){1,3}:255\.255)|(:(:0{0,4}){1,4}:255))$'
    
    if re.match(ipv4_pattern, ip) or re.match(ipv6_pattern, ip):
        return True
    else:
        return False


def extract_as_number(json_string):
    pattern = r'"as":"AS(\d+)'
    match = re.search(pattern, json_string)
    if match:
        return match.group(1), None
    else:
        return None, "ASN for this IP is not found!\nDoublecheck and rerun /ip command"

def get_asn_from_ip(ip):
    try:
        url='http://ip-api.com/json/{}?fields=as'.format(ip)
        response=requests.get(url)
        response.raise_for_status()  # This will raise an HTTPError if the HTTP request returned an unsuccessful status code
        asn, error = extract_as_number(response.text)
        if asn is None:
            return None, error
        return asn, None
    except requests.exceptions.RequestException as e:
        result = f"ASN for this IP is not found!\nDoublecheck and rerun /ip command"
        logging.error(result + ": " + str(e))
        return None, result    

def convert_to_local_time(time):
    utc_time = datetime.fromisoformat(time.replace("Z", "+00:00"))
    server_timezone = pytz.timezone(cfg.TZ)
    local_time =  utc_time.astimezone(server_timezone)
    return local_time

def start_mw():    
    try:        
        api = UptimeKumaApi(cfg.KUMA_HOST)                
        try: 
            api.login(cfg.KUMA_LOGIN, cfg.KUMA_PASSWORD)
            api.resume_maintenance(cfg.KUMA_MW_ID)
            result = "MW has been started"
        except socketio.exceptions.TimeoutError:
            result = "⏱️ Timeout connecting to Uptime Kuma. Service may be slow or unreachable."
            logging.error(result)
        except UptimeKumaException as e:
            result = "An error occurred while resuming MW"
            logging.error(result + ": " + str(e))
        finally:
            try:
                api.disconnect()
            except Exception as e:
                logging.error("Error while disconnecting: " + str(e))
    except socketio.exceptions.TimeoutError:
        result = "⏱️ Timeout connecting to Uptime Kuma. Service may be unreachable."
        logging.error(result)
    except Exception as e:
        result = "Unable to establish connection to Uptime Kuma"
        logging.error(result + ": " + str(e))
    return result  
               
        
def stop_mw():    
    try:        
        api = UptimeKumaApi(cfg.KUMA_HOST)                
        try:             
            api.login(cfg.KUMA_LOGIN, cfg.KUMA_PASSWORD)
            api.pause_maintenance(cfg.KUMA_MW_ID)
            result = "MW has been completed"            
        except socketio.exceptions.TimeoutError:
            result = "⏱️ Timeout connecting to Uptime Kuma. Service may be slow or unreachable."
            logging.error(result)
        except UptimeKumaException as e:
            result = 'An error occurred while pausing MW'            
            logging.error(result + ": " + str(e))
        finally:
            try:
                api.disconnect()
            except Exception as e:
                logging.error("Error while disconnecting: " + str(e))
    except socketio.exceptions.TimeoutError:
        result = "⏱️ Timeout connecting to Uptime Kuma. Service may be unreachable."
        logging.error(result)
    except Exception as e:
        result = 'Unable to establish connection to Uptime Kuma'
        logging.error(result + ": " + str(e))
    return result


def get_asns_from_firewall_rule():    
    # Cloudflare API endpoint for updating a WAF rule
    url = f"https://api.cloudflare.com/client/v4/zones/{cfg.WAF_ZONE}/rulesets/{cfg.WAF_RULESET}"

    # Headers for authentication and content type
    headers = {
        'Authorization': "Bearer " + cfg.WAF_TOKEN
    }
    
    try:
        response = requests.get(url, headers=headers)
        # Check the response status
        if response.status_code != 200:
            result = f"Failed to retrieve the rule. Status code: {response.status_code}"
            logging.error(f"{result}: Response text: {response.text}")
            return None, result
        
        response_dict = response.json()
        rules = response_dict['result']['rules']
            
        for rule in rules:
            if rule.get('id') == cfg.WAF_RULEID:
                expression = rule.get('expression', '')
           
                # Extract ASNs using regular expression
                asns = re.findall(r'\b\d+\b', expression)
                logging.warning("Old Rule: " + ' '.join(map(str, asns)))        

                return asns, None
    
        result = 'Rule retrieved successfully.'
        logging.warning(result)
        return None, result
            
    except Exception as e:
        result = f"Unexpected error occurred: {str(e)}"
        logging.error(result)
        return None, result



def add_asn_to_firewall_rule(asn):    
    subdomain = cfg.CDN_URL

    # Save current firewall ASNs
    old_asns, error = get_asns_from_firewall_rule()

    # If the ASN is not found, return an error
    if old_asns is None:
        result = f"An error occcured while retrieving ASNs from the firewall rule: {error}"
        logging.error(result)
        return result
    
    # Check if the ASN already exists in the list
    if asn not in old_asns:        
        old_asns.append(asn)        
    else:
        result = f"ASN {asn} already exists in the firewall rule."
        logging.warning(result)
        return result

    # Convert the list to a comma-separated string
    new_asns = ' '.join(map(str, old_asns))
    logging.warning("New Rule: " + new_asns)

    # Update the firewall rule data
    rule_data = {
    "action": "skip",
        "action_parameters": {
            "ruleset": "current"
        },
        "expression": "(ip.geoip.asnum in {" + new_asns + "} and http.host wildcard \"" + subdomain + "\")",
        "description": "Whitelist MWBot"
    }

    # Cloudflare API endpoint for updating a WAF rule
    url = f"https://api.cloudflare.com/client/v4/zones/{cfg.WAF_ZONE}/rulesets/{cfg.WAF_RULESET}/rules/{cfg.WAF_RULEID}"

    # Headers for authentication and content type
    headers = {
        'Authorization': "Bearer " + cfg.WAF_TOKEN
    }
    
    try:
        response = requests.patch(url, headers=headers, data=json.dumps(rule_data))
        # Check the response status
        if response.status_code == 200:
            result = f"ASN {asn} has been sucessfully added to the firewall rule."
            logging.warning(result)
        else:
            result = f"Failed to update rule. Status code: {response.status_code}"
            logging.error(f"{result}: Response text: {response.text}")
    except Exception as e:
        result = f"Unexpected error occurred"
        logging.error(result + ": " + str(e))
    return result

def get_rule_status():
    subdomain = cfg.CDN_URL
    # Cloudflare API endpoint for updating a WAF rule
    url = f"https://api.cloudflare.com/client/v4/zones/{cfg.WAF_ZONE}/rulesets/{cfg.WAF_RULESET}"
    
    # Headers for authentication and content type
    headers = {
        'Authorization': "Bearer " + cfg.WAF_TOKEN
    }    
    
    try:
        response = requests.get(url, headers=headers)
        # Check the response status
        if response.status_code != 200:
            result = f"Failed to retrieve the rule. Status code: {response.status_code}"
            logging.error(f"{result}: Response text: {response.text}")
            return None, result
        
        response_dict = response.json()
        rules = response_dict['result']['rules']
            
        for rule in rules:
            if rule.get('id') == cfg.WAF_RULEID:
                expression = rule.get('enabled', '')                
                match expression:
                    case None:
                        result = f"Failed to retrieve the rule. Status code: {response.status_code}"
                        logging.error(f"{result}: Response text: {response.text}")
                        return None, result
                    case True:
                        return True, None
                    case False:
                        return False, None     
       
    except Exception as e:
        result = f"Unexpected error occurred: {str(e)}"
        logging.error(result)
        return None, result

def get_rule_modify_date():
    subdomain = cfg.CDN_URL
    # Cloudflare API endpoint for updating a WAF rule
    url = f"https://api.cloudflare.com/client/v4/zones/{cfg.WAF_ZONE}/rulesets/{cfg.WAF_RULESET}"
    
    # Headers for authentication and content type
    headers = {
        'Authorization': "Bearer " + cfg.WAF_TOKEN
    }    
    
    try:
        response = requests.get(url, headers=headers)
        # Check the response status
        if response.status_code != 200:
            result = f"Failed to retrieve the rule. Status code: {response.status_code}"
            logging.error(f"{result}: Response text: {response.text}")
            return None, result
        
        response_dict = response.json()
        rules = response_dict['result']['rules']
            
        for rule in rules:
            if rule.get('id') == cfg.WAF_RULEID:
                expression = rule.get('last_updated', '')                
                if expression is None:
                        result = f"Failed to retrieve the rule modification date. Status code: {response.status_code}"
                        logging.error(f"{result}: Response text: {response.text}")
                        return None, result
                else:

                    return expression, None
       
    except Exception as e:
        result = f"Unexpected error occurred: {str(e)}"
        logging.error(result)
        return None, result
    
    
def disable_asn_to_firewall_rule():    
    subdomain = cfg.CDN_URL
    default_asn = cfg.MW_BOT_ASN_DEFAULT

    # Update the firewall rule data
    rule_data = {
    "action": "skip",
        "action_parameters": {
            "ruleset": "current"
        },
    "expression": "(ip.geoip.asnum in {" + default_asn + "} and http.host wildcard \"" + subdomain + "\")",
    "description": "Whitelist MWBot",
    "enabled": False
    }

    # Cloudflare API endpoint for updating a WAF rule
    url = f"https://api.cloudflare.com/client/v4/zones/{cfg.WAF_ZONE}/rulesets/{cfg.WAF_RULESET}/rules/{cfg.WAF_RULEID}"

    # Headers for authentication and content type
    headers = {
        'Authorization': "Bearer " + cfg.WAF_TOKEN
    }
    
    try:
        response = requests.patch(url, headers=headers, data=json.dumps(rule_data))
        # Check the response status
        if response.status_code == 200:
            result = f"Firewall rule has been disabled."
            logging.warning(result)
        else:
            result = f"Failed to update rule. Status code: {response.status_code}"
            logging.error(f"{result}: Response text: {response.text}")
    except Exception as e:
        result = f"Unexpected error occurred"
        logging.error(result + ": " + str(e))
    return result

def schedule_fw_task():
    while True:        
        current_time = datetime.now(pytz.timezone(cfg.TZ))        
        next_run = (current_time + timedelta(days=1)).replace(hour=3, minute=40, second=0, microsecond=0)
        #next_run = current_time
        delay = (next_run - current_time).total_seconds()

        print(f"[{current_time}] Next run scheduled at {next_run} (in {delay} seconds)")

        # Sleep until the next run
        time.sleep(delay)
        
        # Check the rule status
        status, error= get_rule_status()
        
        if status is None:
            result = f"An error occcured while retrieving the rule status: {error}"
            logging.error(result)
            continue        
        
        if status:                   
           # Retrieve and convert the rule modification date
           modify_str, error = get_rule_modify_date()
           if modify_str is None:
               logging.error(f"An error occurred while retrieving the rule modification date: {error}")
               continue
           
           modify_local_time = convert_to_local_time(modify_str)
           current_time = datetime.now(pytz.timezone(cfg.TZ))
           
           # Check if the rule needs to be disabled 
           if modify_local_time + timedelta(days=7) < current_time:
               disable_asn_to_firewall_rule()
