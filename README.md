# MWBot

MWBot is a Telegram bot designed to manage maintenance windows (MW) in Uptime Kuma and notify a specified Telegram chat. It allows authorized users to start and stop maintenance windows and perform other related tasks via Telegram commands.

## Features

- Start and stop maintenance windows in Uptime Kuma.
- Notify a specified Telegram chat about maintenance activities.
- Manage IP addresses for access control.
- Authenticate users based on Telegram user IDs.

## Docker Compose Setup

To deploy MWBot using Docker Compose, use the following configuration:

```yaml
version: '3'

services:
  mwbot:
    image: ghcr.io/freender/mwbot:main
    container_name: mwbot
    environment:
      - TOKEN=${TELEGRAM_TOKEN} # Set your Telegram bot token
      - TOKEN_STAGING=${TELEGRAM_TOKEN_STAGING} # Set your Telegram bot token for staging
      - CHAT_ID=${TELEGRAM_CHATID} # Telegram chat ID for notifications
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
```

## Environment Variables

- `TOKEN`: Your Telegram bot token.
- `TOKEN_STAGING`: Your Telegram bot token for the staging environment.
- `CHAT_ID`: The ID of the Telegram chat to receive notifications.
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

## Usage

1. **Start the Bot**: Use the `/start` command to initialize the bot.
2. **Help**: Use the `/help` command to list all available commands.
3. **Manage Maintenance Windows**: Use commands like `/start_silent`, `/stop_silent`, `/firmware_mw`, `/reboot_mw`, `/generic_mw`, and `/stop_mw` to manage maintenance windows.
4. **IP Management**: Use `/ip` to allow a new IP address and `/reset_ip` to reset IP access.

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request with your changes.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
