import logging

import cfg
from modules.firewall import (
    add_asn_to_firewall_rule,
    convert_to_local_time,
    disable_asn_to_firewall_rule,
    get_asn_from_ip,
    get_asns_from_firewall_rule,
    get_next_firewall_run,
    get_rule_modify_date,
    get_rule_status,
    is_valid_ip,
    schedule_fw_task,
)
from modules.maintenance import (
    build_mw_state,
    clear_mw_state,
    delete_message,
    format_duration,
    get_mw_status_text,
    load_mw_state,
    maintain_timed_mw,
    parse_duration,
    replace_mw_state,
    save_mw_state,
    start_mw,
    stop_mw,
    stop_timed_mw,
)
from modules.redownload import (
    ISSUE_STATUS_OPEN,
    ISSUE_STATUS_RESOLVED,
    build_redownload_confirmation,
    build_target_label,
    delete_queue_item,
    execute_redownload,
    find_queue_item,
    find_seerr_issue_for_media,
    get_all_seerr_issue_ids,
    get_episode,
    get_issue_target,
    get_seerr_issue,
    get_seerr_media_details,
    is_issue_open,
    issue_sort_key,
    mark_history_failed,
    parse_seerr_issue_url,
    parse_seerr_reference,
    process_radarr_redownload,
    process_sonarr_redownload,
    resolve_redownload_issue,
    resolve_seerr_issue,
    select_failed_history_record,
)


COMMANDS = {
    'start': 'Start Screen',
    'ip': 'Allow Plex in unknown place',
    'reset_ip': 'Disable Plex in unknown place',
    'redownload': 'Blacklist issue release and prevent re-download',
    'start_silent': 'Start Silent MW [/start_silent 30m]',
    'stop_silent': 'Stop Silent MW',
    'firmware_mw': 'Start Firmware MW and notify Sev1 chat [/firmware_mw 30m]',
    'reboot_mw': 'Start Reboot MW and notify Sev1 chat [/reboot_mw 30m]',
    'generic_mw': 'Start MW and notify Sev1 chat [/generic_mw 30m]',
    'stop_mw': 'Stop MW and notify Sev1 chat',
    'mw_status': 'Show active MW timer status',
}

HELP_SECTIONS = [
    {
        'title': 'Plex Access',
        'icon': '📡',
        'commands': {
            'ip': 'Allow Plex from your current location',
            'reset_ip': 'Remove temporary Plex access',
        },
    },
    {
        'title': 'Media',
        'icon': '🎬',
        'commands': {
            'redownload': 'Replace a bad release via Seerr issue',
        },
    },
    {
        'title': 'Maintenance',
        'icon': '🔧',
        'commands': {
            'start_silent': 'Start silent MW',
            'stop_silent': 'Stop silent MW',
            'firmware_mw': 'Firmware MW + notify Sev1',
            'reboot_mw': 'Reboot MW + notify Sev1',
            'generic_mw': 'Generic MW + notify Sev1',
            'stop_mw': 'Stop MW + notify Sev1',
            'mw_status': 'Show active MW timer',
        },
        'footer': 'Timed: /start_silent 30m, /firmware_mw 2h, etc.',
    },
]


def is_command(string):
    if not string:
        return False
    return string.startswith('/')


def is_owner(message):
    logging.warning('Auth attempt for CID: %s', message.chat.id)
    return bool(message.chat.id == cfg.OWNER)


def is_auth_user(message):
    logging.warning('Auth attempt for CID: %s', message.chat.id)
    return bool(str(message.chat.id) in cfg.TELEGRAM_AUTH_USERS)
