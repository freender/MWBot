import os
import re
import cfg
import telebot
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

def is_owner(message):
    return bool(message.chat.id == cfg.OWNER)

def start_mw():
    api = UptimeKumaApi(cfg.KUMA_HOST)
    api.login(cfg.KUMA_LOGIN,cfg.KUMA_PASSWORD)
    api.resume_maintenance(cfg.KUMA_MW_ID)
    api.disconnect()

def stop_mw():
    api = UptimeKumaApi(cfg.KUMA_HOST)
    api.login(cfg.KUMA_LOGIN,cfg.KUMA_PASSWORD)
    api.pause_maintenance(cfg.KUMA_MW_ID)
    api.disconnect()