# MWBot Repo Notes

- Entry point: `src/main.py`
- Bot pattern: Telegram handlers in `src/main.py`, service logic in `src/modules/`, env config in `src/cfg.py`
- Tests: `python -m unittest tests.test_modules`
- Prefer explicit error handling and focused unit tests for helper flows
- Keep user-facing Telegram replies short and actionable
- Maintenance flow: use `/start` as the menu-only entry point; inline buttons cover silent/regular start, reboot/firmware 5m presets, stop actions, and status
- Redownload flow: open it from the Media menu; ask for a Seerr issue, movie, or series URL; resolve it via Seerr API; if a media URL is sent, use the latest matching Seerr issue; confirm with the user; then blacklist via queue removal first and history fallback second
- Arr routing: standard items use `SONARR_*` / `RADARR_*`; 4K items use `SONARR4K_*` / `RADARR4K_*` when Seerr points at a 4K service
- Deployment note: MWBot needs network reachability to `seerr`, `sonarr`, `sonarr4k`, `radarr`, and `radarr4k`; on helm this is done by attaching the container to `net_overlay`
- Telegram inline URL buttons must use a browser-valid public URL; if Seerr is configured with an internal host like `seerr:5055`, set `SEERR_PUBLIC_URL` for Telegram-facing links
- Access test override: MWBot supports per-user auth override via `SEERR_ACCESS_TEST_USER_ID` + `SEERR_ACCESS_TEST_MODE` in `/mnt/cache/appdata/mwbot/.env` on `helm`; valid modes are empty/`normal`, `authorized`, `unauthorized`, and `owner`
- Access test workflow on `helm`: change `SEERR_ACCESS_TEST_MODE` in `/mnt/cache/appdata/mwbot/.env`, then restart with `ssh helm "docker compose -f /mnt/cache/appdata/mwbot/compose.yml up -d mwbot"`; no rebuild needed for mode switches

## Local Build Workflow

- Develop from `exo`, but do Docker builds on `helm`
- Repo sync target on `helm`: `~/mwbot`
- Runtime compose file on `helm`: `/mnt/cache/appdata/mwbot/compose.yml`
- Local image name on `helm`: `mwbot:local`
- Compose build context on `helm`: `/home/freender/mwbot`

### Standard flow

1. Make code changes on `exo` in `/Users/freender/mwbot`
2. Run quick validation locally when possible:
   - `python3 -m py_compile src/main.py src/modules/redownload.py src/cfg.py src/modules/__init__.py src/modules/firewall.py src/modules/maintenance.py src/modules/common.py tests/test_modules.py`
   - `python3 -m unittest tests.test_modules`
3. Sync repo to `helm`:
   - `rsync -az --delete --exclude ".git" --exclude ".venv" "/Users/freender/mwbot/" "helm:~/mwbot/"`
4. Build on `helm`:
   - `ssh helm "docker compose -f /mnt/cache/appdata/mwbot/compose.yml build mwbot"`
5. Start or refresh the container on `helm`:
   - `ssh helm "docker compose -f /mnt/cache/appdata/mwbot/compose.yml up -d mwbot"`
6. Check logs on `helm` when validating a change:
   - `ssh helm "docker compose -f /mnt/cache/appdata/mwbot/compose.yml logs --tail=200 mwbot"`

### Agent instructions

- Prefer this local `helm` build workflow over pushing to GitHub just to test a change
- Do not change the compose networking unless explicitly asked; `net.internal` and `net_overlay` are required
- When updating the compose file for local testing, keep `image: mwbot:local` and `build.context: /home/freender/mwbot`
- If a user asks for a local build, sync to `helm`, build with compose there, and report the result
