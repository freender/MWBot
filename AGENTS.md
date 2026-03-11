# MWBot Repo Notes

- Entry point: `src/main.py`
- Bot pattern: Telegram handlers in `src/main.py`, service logic in `src/modules/modules.py`, env config in `src/cfg.py`
- Tests: `python -m unittest tests.test_modules`
- Prefer explicit error handling and focused unit tests for helper flows
- Keep user-facing Telegram replies short and actionable
- `/redownload` flow: ask for a Seerr issue, movie, or series URL; resolve it via Seerr API; if a media URL is sent, use the latest matching Seerr issue; confirm with the user; then blacklist via queue removal first and history fallback second
- Arr routing: standard items use `SONARR_*` / `RADARR_*`; 4K items use `SONARR4K_*` / `RADARR4K_*` when Seerr points at a 4K service
- Deployment note: MWBot needs network reachability to `seerr`, `sonarr`, `sonarr4k`, `radarr`, and `radarr4k`; on helm this is done by attaching the container to `net_overlay`
