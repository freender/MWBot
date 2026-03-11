# MWBot

MWBot is a Telegram bot designed to manage maintenance windows (MW) in Uptime Kuma and notify a specified Telegram chat. It allows authorized users to start and stop maintenance windows and perform other related tasks via Telegram commands.

## Features

- Start and stop maintenance windows in Uptime Kuma.
- Optionally start timed maintenance windows with commands like `/generic_mw 30m` or `/start_silent 2h`.
- Auto-stop timed maintenance windows and clean up the notification message when the window completes successfully.
- Notify a dedicated Telegram channel about maintenance activities.
- Support a separate notification chat for testing via environment variable override.
- Manage IP addresses for access control.
- Authenticate users based on Telegram chat IDs.
- Interactive `/redownload` workflow for Seerr issue, movie, or series URLs that blocklists bad Sonarr/Radarr releases.

## Docker Compose Setup

To deploy MWBot using Docker Compose, use the following configuration:

```yaml
version: '3'

services:
  mwbot:
    image: ghcr.io/<your-ghcr-user>/mwbot:main
    container_name: mwbot
    environment:
      - TOKEN=${TELEGRAM_TOKEN} # Set your Telegram bot token
      - TOKEN_STAGING=${TELEGRAM_TOKEN_STAGING} # Set your Telegram bot token for staging
      - CHAT_ID=${TELEGRAM_CHATID} # Default Telegram chat ID for notifications
      - NOTIFY_CHAT_ID=${TELEGRAM_NOTIFY_CHATID} # Optional override for maintenance notifications
      - OWNER=${OWNER} # Telegram user ID of the bot owner
      - KUMA_HOST=${UPTIME_HOST} # Uptime Kuma IP address and port
      - KUMA_LOGIN=${UPTIME_LOGIN} # Uptime Kuma login
      - KUMA_PASSWORD=${UPTIME_PASSWORD} # Uptime Kuma password
      - KUMA_MW_ID=${UPTIME_MW_ID} # ID of the maintenance window to manage
      - WAF_TOKEN=${WAF_TOKEN} # Cloudflare WAF API token
      - WAF_ZONE=${WAF_ZONE} # Cloudflare WAF zone ID
      - WAF_RULESET=${WAF_RULESET} # Cloudflare WAF ruleset ID
      - WAF_RULEID=${WAF_RULEID} # Cloudflare WAF rule ID
      - CDN_URL=${CDN_URL} # CDN URL for firewall rules
      - TELEGRAM_AUTH_USERS=${TELEGRAM_AUTH_USERS} # JSON list of authorized Telegram user IDs
      - MW_BOT_ASN_DEFAULT=${MW_BOT_ASN_DEFAULT} # Default ASN for MWBot
      - TZ=${TIMEZONE} # Server timezone
      - SEERR_BASE_URL=${SEERR_BASE_URL} # Seerr base URL
      - SEERR_API_KEY=${SEERR_API_KEY} # Seerr API key
      - SONARR_BASE_URL=${SONARR_BASE_URL} # Sonarr base URL
      - SONARR_API_KEY=${SONARR_API_KEY} # Sonarr API key
      - RADARR_BASE_URL=${RADARR_BASE_URL} # Radarr base URL
      - RADARR_API_KEY=${RADARR_API_KEY} # Radarr API key
      - SONARR4K_BASE_URL=${SONARR4K_BASE_URL} # Optional Sonarr 4K base URL
      - SONARR4K_API_KEY=${SONARR4K_API_KEY} # Optional Sonarr 4K API key
      - RADARR4K_BASE_URL=${RADARR4K_BASE_URL} # Optional Radarr 4K base URL
      - RADARR4K_API_KEY=${RADARR4K_API_KEY} # Optional Radarr 4K API key
```

## Environment Variables

- `TOKEN`: Your Telegram bot token.
- `TOKEN_STAGING`: Your Telegram bot token for the staging environment.
- `CHAT_ID`: The default Telegram chat ID to receive notifications.
- `NOTIFY_CHAT_ID`: Optional notification chat override. If unset, `CHAT_ID` is used.
- `OWNER`: The Telegram user ID of the bot owner, who has full command access.
- `KUMA_HOST`: The IP address and port of your Uptime Kuma instance.
- `KUMA_LOGIN`: The login username for Uptime Kuma.
- `KUMA_PASSWORD`: The password for Uptime Kuma.
- `KUMA_MW_ID`: The ID of the maintenance window to manage in Uptime Kuma.
- `WAF_TOKEN`: The API token for Cloudflare WAF.
- `WAF_ZONE`: The zone ID for Cloudflare WAF.
- `WAF_RULESET`: The ruleset ID for Cloudflare WAF.
- `WAF_RULEID`: The rule ID for Cloudflare WAF.
- `CDN_URL`: The CDN URL used in firewall rules.
- `TELEGRAM_AUTH_USERS`: A JSON-encoded list of authorized Telegram user IDs.
- `MW_BOT_ASN_DEFAULT`: The default ASN for MWBot.
- `TZ`: The server's timezone.
- `SEERR_BASE_URL`: Base URL for Seerr.
- `SEERR_API_KEY`: API key for Seerr issue lookups.
- `SONARR_BASE_URL`: Base URL for Sonarr.
- `SONARR_API_KEY`: API key for Sonarr queue/history access.
- `RADARR_BASE_URL`: Base URL for Radarr.
- `RADARR_API_KEY`: API key for Radarr queue/history access.
- `SONARR4K_BASE_URL`: Optional Sonarr 4K base URL. Defaults to `SONARR_BASE_URL`.
- `SONARR4K_API_KEY`: Optional Sonarr 4K API key. Defaults to `SONARR_API_KEY`.
- `RADARR4K_BASE_URL`: Optional Radarr 4K base URL. Defaults to `RADARR_BASE_URL`.
- `RADARR4K_API_KEY`: Optional Radarr 4K API key. Defaults to `RADARR_API_KEY`.

## Usage

1. **Start the Bot**: Use the `/start` command to initialize the bot.
2. **Help**: Use the `/help` command to list all available commands.
3. **Manage Maintenance Windows**:
   - Use `/start_silent`, `/stop_silent`, `/firmware_mw`, `/reboot_mw`, `/generic_mw`, and `/stop_mw` to manage maintenance windows.
   - Add a duration such as `30m` or `2h` to `/start_silent`, `/firmware_mw`, `/reboot_mw`, or `/generic_mw` for timed cleanup.
   - Use `/mw_status` to inspect the active timed maintenance window.
4. **IP Management**: Use `/ip` to allow a new IP address and `/reset_ip` to reset IP access.
5. **Redownload Control**: Use `/redownload` and follow the prompts with a Seerr issue, movie, or series URL. The bot confirms the target, then blocklists the matching release in Sonarr or Radarr so it is not downloaded again.

## How Redownload Works

1. Send `/redownload` in Telegram.
2. The bot asks for a Seerr URL such as `https://seerr.example.com/issues/29`, `https://seerr.example.com/movie/1220564`, or `https://seerr.example.com/tv/1408`.
3. If you send a movie or series URL, the bot looks up the most recent matching Seerr issue automatically, then resolves the target media plus whether it belongs to the standard or 4K arr instance.
4. The bot shows a confirmation message with the selected backend: `Radarr`, `Radarr4k`, `Sonarr`, or `Sonarr4k`.
5. After you reply `yes`, the bot tries to stop future grabs in this order:
   - remove a matching queued release with `blocklist=true` and `skipRedownload=true`
   - if nothing is queued, mark the best matching history item as failed
6. For history fallback, the bot prefers `grabbed` records before `downloadFolderImported` records so the blocklist entry is created against the actual grabbed release.

## Deployment Notes

- MWBot must be able to resolve and reach `seerr`, `sonarr`, `sonarr4k`, `radarr`, and `radarr4k` over Docker networking.
- In the current homelab deployment, `mwbot` is attached to both `net.internal` and `net_overlay` so it can talk to the arr containers on tower.
- If you only run one Sonarr or Radarr instance, the optional `SONARR4K_*` and `RADARR4K_*` values can be omitted and will fall back to the standard endpoints.

## Timed Maintenance Windows

- Timed maintenance windows are persisted to `/config/mw_state.json`.
- When a timed maintenance window expires, the bot stops the Uptime Kuma maintenance window automatically.
- If the maintenance notification was sent to the notification chat, the bot deletes that message after a successful timed completion to keep the channel clean.
- Failure messages are left in chat so cleanup problems remain visible.

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request with your changes.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
