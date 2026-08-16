# MWBot

MWBot is a Telegram bot for Alertmanager alert handling and media/Plex access controls. It uses a menu-first Telegram flow, with `/start` opening the inline button menu for all supported actions.

## Features

- An owner-only Alerts section where the live Alertmanager alert list *is* the menu: pick an alert to file it as an incident, resolve it, or silence it.
- Per-alert silences that match only that alert's label set, alongside a blanket maintenance window for the whole homelab.
- Alerts already filed are marked `→ #N` on the list, so a known fault is visible before you tap anything.
- Manage ISP ASN access through Cloudflare WAF.
- Detect a user's Cloudflare-observed ASN through a short-lived Worker session.
- Authenticate users from Seerr Telegram notification settings.
- Interactive redownload workflow from the Media menu for Seerr issue, movie, or series URLs that blocklists bad Sonarr/Radarr releases.
- Owner-only incident creation in the private `homelab-ops` repository, filed from a firing Alertmanager alert.

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
      - CHAT_ID=${TELEGRAM_CHATID} # Default Telegram chat ID for notifications
      - ALERTMANAGER_URL=${ALERTMANAGER_URL} # Optional Alertmanager API base URL
      - ALERTMANAGER_MW_MATCHERS=${ALERTMANAGER_MW_MATCHERS} # Optional silence matcher JSON
      - ALERTMANAGER_OPEN_MW_DURATION=${ALERTMANAGER_OPEN_MW_DURATION} # Optional maintenance safety expiry; defaults to 12h
      - ALERTMANAGER_ALERT_SILENCE_DURATIONS=${ALERTMANAGER_ALERT_SILENCE_DURATIONS} # Optional per-alert silence choices; defaults to 1d,3d,7d
      - GITHUB_INCIDENT_REPO=${GITHUB_INCIDENT_REPO} # Private incident repository
      - GITHUB_INCIDENT_TOKEN=${GITHUB_INCIDENT_TOKEN} # Fine-grained token with Issues write access
      - WAF_TOKEN=${WAF_TOKEN} # Cloudflare WAF API token
      - WAF_ZONE=${WAF_ZONE} # Cloudflare WAF zone ID
      - WAF_RULESET=${WAF_RULESET} # Cloudflare WAF ruleset ID
      - WAF_RULEID=${WAF_RULEID} # Cloudflare WAF rule ID
      - CDN_URL=${CDN_URL} # CDN URL for firewall rules
      - MW_BOT_ASN_DEFAULT=${MW_BOT_ASN_DEFAULT} # Default ASN for MWBot
      - ACCESS_CHECK_API_URL=${ACCESS_CHECK_API_URL} # Optional Cloudflare network-check Worker URL
      - ACCESS_CHECK_API_TOKEN=${ACCESS_CHECK_API_TOKEN} # Shared Worker API token
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
- `CHAT_ID`: The default Telegram chat ID to receive notifications.
- `ALERTMANAGER_URL`: Optional Alertmanager API base URL used by the Alerts section.
- `ALERTMANAGER_MW_MATCHERS`: Optional JSON array of matchers for the blanket maintenance window. Defaults to all alerts. Per-alert silences do not use this; they match the selected alert's own label set.
- `ALERTMANAGER_OPEN_MW_DURATION`: Maintenance window safety expiry. Defaults to `12h`.
- `ALERTMANAGER_ALERT_SILENCE_DURATIONS`: Comma-separated durations offered when silencing one alert, in button order. Defaults to `1d,3d,7d`. Durations use `m`, `h` or `d`.
- `GITHUB_INCIDENT_REPO`: Private GitHub repository used for incidents. Defaults to `freender/homelab-ops`.
- `GITHUB_INCIDENT_TOKEN`: Fine-grained token limited to that repository with Issues read/write access.
- `WAF_TOKEN`: The API token for Cloudflare WAF.
- `WAF_ZONE`: The zone ID for Cloudflare WAF.
- `WAF_RULESET`: The ruleset ID for Cloudflare WAF.
- `WAF_RULEID`: The rule ID for Cloudflare WAF.
- `CDN_URL`: The CDN URL used in firewall rules.
- `MW_BOT_ASN_DEFAULT`: The default ASN for MWBot.
- `ACCESS_CHECK_API_URL`: Optional base URL for the Cloudflare network-check Worker.
- `ACCESS_CHECK_API_TOKEN`: Shared secret for MWBot-to-Worker API calls. Automatic detection is enabled only when both Worker values are configured.
- `TZ`: The server's timezone.
- `SEERR_BASE_URL`: Base URL for Seerr.
- `SEERR_API_KEY`: API key for Seerr issue lookups and Telegram access sync.
- `SONARR_BASE_URL`: Base URL for Sonarr.
- `SONARR_API_KEY`: API key for Sonarr queue/history access.
- `RADARR_BASE_URL`: Base URL for Radarr.
- `RADARR_API_KEY`: API key for Radarr queue/history access.
- `SONARR4K_BASE_URL`: Optional Sonarr 4K base URL. Defaults to `SONARR_BASE_URL`.
- `SONARR4K_API_KEY`: Optional Sonarr 4K API key. Defaults to `SONARR_API_KEY`.
- `RADARR4K_BASE_URL`: Optional Radarr 4K base URL. Defaults to `RADARR_BASE_URL`.
- `RADARR4K_API_KEY`: Optional Radarr 4K API key. Defaults to `RADARR_API_KEY`.

## Usage

1. **Open the Bot**: Use `/start` to open the main menu.
2. **Alerts**: Owner-only. Opens the live Alertmanager alert list, summarised above it and annotated with the open incident already filed for each alert. Pick an alert for its action sheet:
    - **File Incident** — see Incidents below. Replaced by **Open Incident #N** when one already exists.
    - **Resolve** — offered only for one-shot event alerts (by default `source=pve`). A metric alert clears itself once the condition ends, and would be re-sent by vmalert within one evaluation interval. Confirmation is required, because a resolved alert cannot be brought back.
    - **Silence / Unsilence** — mutes just that alert by matching its exact label set, after asking for how long (`1d` / `3d` / `7d` by default). Unsilence lifts only silences MWBot created for that alert, never the blanket maintenance window. A silenced alert stays in the list marked `🔇 6d`, so a long silence is visible rather than forgotten.

   The list footer starts or ends the blanket maintenance window; only the one that applies to the current state is shown.
3. **Plex Access**: Open the Plex Access section and tap the network-check link. MWBot applies the detected ISP ASN automatically, then updates the same menu with an explicit success or failure result.
4. **Redownload Control**: Open the Media section and follow the prompts. The bot confirms the target, then blocklists the matching release in Sonarr or Radarr so it is not downloaded again.
5. **Incidents**: Owner-only, and filed from a firing alert only. Reachable from an alert's action sheet, or in one tap from `/incident`, which lists the firing alerts as direct filing buttons. The incident carries the alert's labels, annotations, and firing time. There is no free-text path: any text after `/incident` and any replied-to message is ignored, and the flow stops with a message when Alertmanager is unreachable or nothing is firing. This keeps the issue body machine-generated, which is what the triage agent reasons over. The issue is created with the `incident` label, and MWBot posts a `/oc` comment that triggers read-only OpenCode triage in that repository. Filing the same alert twice is refused: the alert fingerprint in the issue body is both the dedup key and what draws `→ #N` on the alert list.

## How Redownload Works

1. Send `/start` in Telegram.
2. Open the Media section and choose the redownload action.
3. The bot asks for a Seerr URL such as `https://seerr.example.com/issues/29`, `https://seerr.example.com/movie/1220564`, or `https://seerr.example.com/tv/1408`.
4. If you send a movie or series URL, the bot looks up the most recent matching Seerr issue automatically, then resolves the target media plus whether it belongs to the standard or 4K arr instance.
5. The bot shows a confirmation message with the selected backend: `Radarr`, `Radarr4k`, `Sonarr`, or `Sonarr4k`.
6. After you confirm in Telegram, the bot tries to stop future grabs in this order:
    - remove a matching queued release with `blocklist=true` and `skipRedownload=true`
    - if nothing is queued, mark the best matching history item as failed
7. For history fallback, the bot prefers `grabbed` records before `downloadFolderImported` records so the blocklist entry is created against the actual grabbed release.

## Deployment Notes

- MWBot must be able to resolve and reach `seerr`, `sonarr`, `sonarr4k`, `radarr`, and `radarr4k` over Docker networking.
- In the current homelab deployment, `mwbot` is attached to both `net.internal` and `net_overlay` so it can talk to the arr containers on tower.
- If you only run one Sonarr or Radarr instance, the optional `SONARR4K_*` and `RADARR4K_*` values can be omitted and will fall back to the standard endpoints.
- Network detection uses the Worker in `worker/`; see `worker/README.md`. Both Worker environment variables are required for Plex access grants.
- The temporary WAF rule permits the ASN reported directly by Cloudflare, using the same classification as the WAF `ip.geoip.asnum` field.

## Silences

- Blanket maintenance-window state is persisted to `/config/alertmanager_mw_state.json`, and every maintenance silence has a safety expiry.
- Per-alert silences are deliberately stateless: they are identified in Alertmanager by their comment, so Unsilence reads the alert's own `silencedBy` list rather than a local file that a restart could lose.
- The per-alert silence floor is one day. A sub-day silence does not survive a nightly re-alert, so it defers the same interruption to tomorrow rather than stopping it. The ceiling is one week, because nothing re-notifies when a silence lapses.
- A silenced alert still appears in the list, marked with the time left on its silence. Silencing removes an alert from your attention; nothing else puts it back.

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request with your changes.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
