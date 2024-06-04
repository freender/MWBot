import re
import json
import requests
import cfg
import logging
from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api import UptimeKumaException


COMMANDS = {
'start': 'Start Screen',
'ip': 'Allow Plex in unknown place',
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
    logging.warning(f"audit: Auth attempt for USER: {message.chat.id}")
    return bool(message.chat.id == cfg.OWNER)

def is_auth_user(message):
    logging.warning(f"audit: Auth attempt for USER: {message.chat.id}")
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
        return match.group(1)
    else:
        return "None"

def get_asn_from_ip(ip):
    try:
        url='http://ip-api.com/json/{}?fields=as'.format(ip)
        response=requests.get(url)
        response.raise_for_status()  # This will raise an HTTPError if the HTTP request returned an unsuccessful status code
        asn = extract_as_number(response.text)
        return asn
    except requests.exceptions.RequestException as e:
        logging.error(f"An error occurred: {e}")
        return None    

def start_mw():    
    try:        
        api = UptimeKumaApi(cfg.KUMA_HOST)
        api.login(cfg.KUMA_LOGIN, cfg.KUMA_PASSWORD)
        try: 
            api.resume_maintenance(cfg.KUMA_MW_ID)
            result = "MW has been started"            
        except UptimeKumaException as e:
            logging.error(f"An error occurred while resuming MW: {e}")
            result = 'An error occurred while resuming MW'
        finally:
            try:
                api.disconnect()
            except UptimeKumaException as e:
                logging.error(f"An error occurred while disconnecting: {e}")
                result = 'An error occurred while disconnecting'
    except UptimeKumaException as e:
        logging.error(f"An error occurred: {e}")
        result = 'Unable to establish connection to Uptime Kuma'       
    return result  
               
        
def stop_mw():    
    try:        
        api = UptimeKumaApi(cfg.KUMA_HOST)
        api.login(cfg.KUMA_LOGIN, cfg.KUMA_PASSWORD)
        try: 
            api.pause_maintenance(cfg.KUMA_MW_ID)
            result = "MW has been completed"            
        except UptimeKumaException as e:
            logging.error(f"An error occurred while pausing MW: {e}")
            result = 'An error occurred while pausing MW'
        finally:
            try:
                api.disconnect()
            except UptimeKumaException as e:
                logging.error(f"An error occurred while disconnecting: {e}")
                result = 'An error occurred while disconnecting'
    except UptimeKumaException as e:       
        logging.error(f"An error occurred: {e}")
        result = 'Unable to establish connection to Uptime Kuma'       
    return result

def add_asn_to_firewall_rule(asn):    
    subdomain = cfg.CDN_URL

    # Update the firewall rule data
    rule_data = {
    "action": "skip",
        "action_parameters": {
            "ruleset": "current"
        },
        "expression": "(ip.geoip.asnum in {" + asn + "} and http.host eq \"" + subdomain + "\")",
        "description": "Allow MWBot Whitelist"
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
            logging.info("Rule updated successfully.")
            result = 'Rule updated successfully.'
        else:
            logging.error(f"Failed to update rule. Status code: {response.status_code}")
            logging.error(f"Response text: {response.text}")
            result = f"Failed to update rule. Status code: {response.status_code}"
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        result = f"Unexpected error occurred. Check exception message in logs"
    return result
