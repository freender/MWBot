import os
import re
from uptime_kuma_api import UptimeKumaApi
    

COMMANDS = {
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

def start_mw():
    KUMA_HOST = os.environ['KUMA_HOST']
    KUMA_LOGIN = os.environ['KUMA_LOGIN']
    KUMA_PASSWORD = os.environ['KUMA_PASSWORD']
    KUMA_MW_ID = os.environ['KUMA_MW_ID']
    api = UptimeKumaApi(KUMA_HOST)
    api.login(KUMA_LOGIN,KUMA_PASSWORD)
    api.resume_maintenance(KUMA_MW_ID)
    api.disconnect()

def stop_mw():
    KUMA_HOST = os.environ['KUMA_HOST']
    KUMA_LOGIN = os.environ['KUMA_LOGIN']
    KUMA_PASSWORD = os.environ['KUMA_PASSWORD']
    KUMA_MW_ID = os.environ['KUMA_MW_ID']
    api = UptimeKumaApi(KUMA_HOST)
    api.login(KUMA_LOGIN,KUMA_PASSWORD)
    api.pause_maintenance(KUMA_MW_ID)
    api.disconnect()
