# MWBot Repo Notes

- Entry point: `src/main.py`
- Bot pattern: Telegram handlers in `src/main.py`, service logic in `src/modules/`, env config in `src/cfg.py`
- Tests: `python -m unittest tests.test_modules tests.test_main`
- Prefer explicit error handling and focused unit tests for helper flows
- Keep user-facing Telegram replies short and actionable
- Alerts flow: use `/start` as the menu-only entry point; the owner-only Alerts section is one menu over one noun (a firing alert), where the live list *is* the menu and file/resolve/silence are actions on the alert you picked. Maintenance windows and incident filing were separate top-level menus and are not to be split apart again — they rendered the same alert list three ways and let you see an alert in one place while only acting on it in another
- Alert eligibility is decided per alert at render time (`is_resolvable`, `alert_silence_ids`), never by filtering the list: one `get_alert_choices()` feeds the whole section, so an alert offered by one action cannot be missing from another
- Per-alert silences match the alert's exact label set and carry `ALERT_SILENCE_COMMENT`. That comment is load-bearing: it is how Unsilence expires a silence we made for one alert without lifting the blanket maintenance window suppressing all of them
- Silence duration is asked, not assumed (`ALERTMANAGER_ALERT_SILENCE_DURATIONS`, default `1d,3d,7d`). One day is a floor, not a default to shrink: a sub-day silence does not survive a nightly re-alert, so it defers tomorrow's interruption instead of stopping it. One week is a ceiling because **nothing re-notifies when a silence lapses**
- A silenced alert keeps its row in the list, marked `🔇 <time left>`. Silencing is the one action that removes an alert from your attention, and for a multi-day silence that visibility is the only thing bringing it back. Do not "tidy" silenced alerts out of the list
- Resolve exists for a one-shot event with no live condition; Silence exists for a live condition already tracked by an incident. Do not merge them: resolving a metric alert would only have vmalert re-send it
- `cfg._parse_duration` accepts `m`/`h`/`d`. `cfg` is evaluated at import, so a bad duration in the runtime `.env` raises and **crash-loops the container** rather than falling back — that is deliberate (a silently wrong silence length is worse), but it means duration config changes need a canary, not a blind restart
- `get_alertmanager_window_text()` returning `''` means "no window active" and is what the Start/End Maintenance button keys off. It also clears state whose silence has already expired, so read it before building the keyboard

## Monitoring Is Owner-Only, By Registration

The whole alerts surface — the list, every per-alert action, the maintenance window, incident
filing — is owner-only, and `handle_callback` enforces it **before dispatch**:

- `ALERT_CALLBACK_HANDLERS` is owner-only in its entirety. Only alert actions belong in it;
  putting anything else there silently makes that thing owner-only.
- `OWNER_ONLY_CALLBACKS` holds the fixed-name owner callbacks. A new monitoring callback goes
  in one of those two places, and that registration is what gates it.
- Handlers keep their own `_require_owner_callback` because they are also called directly.
  That is defence in depth, not the primary gate — never the only one.

Why it is registration rather than a line in each handler: an Alertmanager silence or
resolution changes what the homelab will tell **anyone** about itself, and filing an incident
writes to a private repo and triggers a triage run against real hosts. "Remember to add the
check" is not an access control.

`tests/test_main.py::OwnerOnlySurfaceTest` enforces this and is derived from the dispatch maps,
so a newly registered action is covered the moment it exists. Its load-bearing test is
`test_no_callback_at_all_reaches_monitoring_for_a_non_owner`: it stubs only the network
boundary and the non-monitoring flows, runs every registered callback as a non-owner, and
asserts nothing reached `modules.alertmanager` or the incident repo. It asks whether monitoring
was reached, not whether a declaration was written, so a monitoring callback added with no gate
*and* no declaration still fails. Do not "simplify" it by stubbing the monitoring path.

Identity is Seerr-derived (`is_owner_chat_id`, Seerr user `id=1`). An unreachable Seerr empties
the owner set, which must keep failing closed: no owner means the section is shut, never open.
- Redownload flow: open it from the Media menu; ask for a Seerr issue, movie, or series URL; resolve it via Seerr API; if a media URL is sent, use the latest matching Seerr issue; confirm with the user; then blacklist via queue removal first and history fallback second
- Arr routing: standard items use `SONARR_*` / `RADARR_*`; 4K items use `SONARR4K_*` / `RADARR4K_*` when Seerr points at a 4K service
- Deployment note: MWBot needs network reachability to `seerr`, `sonarr`, `sonarr4k`, `radarr`, and `radarr4k`; on helm this is done by attaching the container to `net_overlay`
- Telegram inline URL buttons must use a browser-valid public URL; if Seerr is configured with an internal host like `seerr:5055`, set `SEERR_PUBLIC_URL` for Telegram-facing links
- Access source of truth: MWBot authorizes users from Seerr notification `telegramChatId` values; Seerr user `id=1` is treated as owner
- Plex network detection: the Cloudflare Worker under `worker/` records Cloudflare's client ASN and optional AS organization in a five-minute KV session; MWBot polls it, grants that ASN in the WAF rule, and sends an explicit success/failure result; manual IP entry is retired
- Access test override: MWBot supports per-user auth override via `SEERR_ACCESS_TEST_USER_ID` + `SEERR_ACCESS_TEST_MODE` in `/mnt/cache/appdata/mwbot/.env` on `helm`; valid modes are empty/`normal`, `authorized`, `unauthorized`, and `owner`
- Access test workflow on `helm`: change `SEERR_ACCESS_TEST_MODE` in `/mnt/cache/appdata/mwbot/.env`, then restart with `ssh helm "docker compose -f /mnt/cache/appdata/mwbot/compose.yml up -d mwbot"`; no rebuild needed for mode switches

## Public Repo And Secrets

- This repository is public. Never commit runtime `.env` files, API tokens, real Telegram IDs, Cloudflare account/zone/ruleset/KV IDs, or real public route hostnames; use placeholders.
- `worker/wrangler.toml` is ignored and contains account-specific deployment metadata. Keep it present in the authoring checkout for deploys, sync it to `helm`, and never stage it. Commit only `worker/wrangler.toml.example`.
- Runtime secrets remain in `/mnt/cache/appdata/mwbot/.env` on `helm` with mode `0600`. Do not read, print, copy into the repo, or include their values in logs; pass that file to containers with `--env-file`.
- The shared Worker API secret is distinct from the Cloudflare deployment/WAF token. Worker browser responses and authenticated API responses must never expose client IPs; ASN and optional AS organization are the only network metadata MWBot stores.

## Incident Pipeline Contract

MWBot is the **producer** for an incident pipeline whose other half lives elsewhere.
`src/modules/incidents.py` opens an issue in `GITHUB_INCIDENT_REPO`, then starts a triage
**session on an OpenCode server** (`OPENCODE_URL`) and links it in Telegram via OpenChamber
(`OPENCHAMBER_PUBLIC_URL`). The owner takes the session over interactively; MWBot only
starts it, links it, and posts its report. These are load-bearing:

- **Repo.** `GITHUB_INCIDENT_REPO` (`src/cfg.py`) defaults to `freender/homelab-ops`.
- **Agent and directory.** The session is created with `agent=OPENCODE_INCIDENT_AGENT` in
  `location.directory=OPENCODE_INCIDENT_DIRECTORY`; the directory decides which project
  agents exist. That agent's own permissions are the read-only boundary between untrusted
  alert text and a shell that reaches every homelab host. **Never send a `permissions`
  override with the session** — `test_create_incident_opens_issue_and_starts_a_triage_session`
  asserts there is none.
- **The prompt marks the incident untrusted.** `build_triage_prompt` fences the title and
  body and says so. Keep that framing if you reword it.
- **Alert fingerprint marker.** `build_incident_body` appends
  `<!-- alert-fingerprint: <fp> -->`. It is how we refuse to file the same firing alert
  twice and how the alerts list draws `→ #N` on an alert that is already filed. It is
  appended after the alert text is truncated so a long alert cannot push it out of the body;
  keep it that way. `get_open_incident_index` reads it back, validating the same shape the
  writer is allowed to emit.
- **Session marker.** Once a session starts, the issue body is edited to carry a
  `Triage session:` link line and `<!-- opencode-session: ses_... -->`. The index reads it
  back, which is what gives an already-filed alert its **Open Triage Session** button and
  what makes a repeat filing reuse the session instead of starting a second investigation.
  A session the server answers 404 for is replaced; one we could not check is assumed alive.
- **Incident index caching.** `get_open_incident_index(use_cache=True)` exists for menu
  renders, which happen in bursts (list → action sheet → Back → list, plus Refresh). Dedup
  must never opt in: `find_open_incident` gates whether a duplicate issue gets filed, and a
  cached "not filed" from thirty seconds ago is exactly the stale answer the unindexed
  listing exists to avoid. `create_incident` invalidates the cache after filing so the list
  you return to shows the new number.
- **Report heading.** `poll_triage_sessions` treats a session as done when it is not in
  `/api/session/active` **and** its newest assistant message contains `## Verdict` — not
  merely when it goes idle, because an agent waiting on a subagent goes idle and wakes
  again. It then posts the report on the issue (owner token) and announces the verdict.
  Renaming that heading in the agent silently stops both. `failed` posts `## Triage failed`;
  a deadline (`OPENCODE_TRIAGE_TIMEOUT_SECONDS`) only notifies, since a long session is
  usually one the owner has taken over. `interrupted` or deleted sessions end quietly.
- **No action buttons.** `_announce_triage_session` carries URL buttons only. Acting on a
  diagnosis happens in the session, by the owner; a callback here would turn a chat tap into
  an instruction to an agent with a shell on every homelab host.
- **Owner chat only.** `_announce_triage_session` sends only to `get_owner_chat_ids()` (the
  same Seerr-derived identity that gates incident filing), never `cfg.CHAT_ID`, a shared
  alert-broadcast chat other people are in. The verdict describes the homelab's faults.

Triage being unconfigured or unreachable never blocks filing: the issue is still created and
the Telegram reply carries the reason as a warning. `tests/test_modules.py` covers the
markers, the session request shape, reuse, and the watcher's end conditions; the server's
real API shape is not testable from here, so verify a live filing after changing it.

On the authoring host, `opencode.json` references the consuming repo as `homelab-ops` for
read access. That repo is private: do not copy its agent internals, host details, or
infrastructure notes into this public repository, and keep `OPENCODE_URL`,
`OPENCODE_INCIDENT_DIRECTORY` and `OPENCHAMBER_PUBLIC_URL` out of it — they have no defaults
on purpose.

## Validation

Run before every local deploy and again before committing. The suite imports `telebot`, which
is only present in the virtualenv, so activate it first — a bare `python3` fails every test
with `ModuleNotFoundError: No module named 'telebot'` rather than reporting a real problem:

```bash
source .venv/bin/activate
python3 -m py_compile src/main.py src/cfg.py src/modules/__init__.py \
  src/modules/alertmanager.py src/modules/common.py src/modules/firewall.py \
  src/modules/maintenance.py src/modules/incidents.py src/modules/network_check.py \
  src/modules/redownload.py tests/test_main.py tests/test_modules.py
python3 -m unittest tests.test_modules tests.test_main
git diff --check
```

`.venv` is gitignored and platform-specific. If it is missing, or was created on a different
OS and copied here, its interpreter symlink dangles and every test errors on import; rebuild
it with `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

The Worker has no npm dependencies. Run `npm test` from `worker/` when Node is available. On
`riven`, sync first and use the disposable Node container on `helm`:

```bash
ssh helm "docker run --rm -v /home/freender/mwbot/worker:/work -w /work node:22-alpine npm test"
```

CI runs the Worker suite, builds/pushes `linux/amd64` and `linux/arm64` images to
`ghcr.io/freender/mwbot:main`, and signs the resulting digest via
`.github/workflows/docker-publish.yml`.

## Deployment Targets

- Repo sync target: `/home/freender/mwbot` on `helm`
- Runtime compose: `/mnt/cache/appdata/mwbot/compose.yml` on `helm`
- Local canary image: `mwbot:local`, built from `/home/freender/mwbot`
- Production image: `ghcr.io/freender/mwbot:main`
- Worker source/config: `/home/freender/mwbot/worker` on `helm`
- Worker deploy auth: existing runtime `.env`; map `MW_BOT_WAF_TOKEN` to `CLOUDFLARE_API_TOKEN` only inside the disposable Wrangler container

Sync without copying Git metadata or virtualenv artifacts:

```bash
rsync -az --delete --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  ./ helm:/home/freender/mwbot/
```

Do not change the compose networks: `net.internal` and `net_overlay` are required.

## Local Canary

Prefer a local Helm canary over pushing merely to test bot code:

1. Sync the checkout to `helm`.
2. Change only the live compose image/build fields to `image: mwbot:local` and
   `build.context: /home/freender/mwbot`; preserve environment, env file, networks, limits,
   and volumes.
3. Run:

   ```bash
   ssh helm "docker compose -f /mnt/cache/appdata/mwbot/compose.yml build mwbot"
   ssh helm "docker compose -f /mnt/cache/appdata/mwbot/compose.yml up -d mwbot"
   ssh helm "docker logs --since 5m --tail 200 mwbot"
   ```

4. Verify the behavior changed, container restart count is zero, Seerr authorization loaded,
   and `network_check_is_configured()` is true when the Worker flow is in scope.

For Worker changes, run its tests and deploy from the synced checkout:

```bash
ssh helm "docker run --rm --env-file /mnt/cache/appdata/mwbot/.env \
  -v /home/freender/mwbot/worker:/work -w /work node:22-alpine \
  sh -c 'CLOUDFLARE_API_TOKEN=\"\$MW_BOT_WAF_TOKEN\" exec npx --yes wrangler deploy'"
```

Verify a real short-lived session from inside `mwbot`: unauthenticated session reads return
`401`; the completion page is dark and sends `no-store`, CSP, and frame-denial headers; the
authenticated result contains `status`, `asn`, optional `as_organization`, and never `ip`;
deletion consumes the session. Never print the session ID or ASN during verification. Confirm
the WAF expression contains `ip.geoip.asnum` and no `ip.src`.

## Shipping (`/ship`)

`.opencode/command/ship.md` runs validate -> local canary/Worker deploy -> verify -> commit ->
push -> CI -> production-image reconciliation. Invocation is the human decision to perform
the live deploy.

1. **Scope.** Infer `bot`, `worker`, or both from `$ARGUMENTS` and the working-tree diff.
   Unrelated dirty files do not block shipping and must not be staged.
2. **Validate.** Run all checks in **Validation**. Fix direct in-scope failures and rerun;
   otherwise stop.
3. **Canary/deploy.** Sync to `helm`. Locally build/recreate `mwbot` when Python or Docker
   content changed. Deploy the Worker only when `worker/` changed. Never expose or stage
   runtime credentials or `worker/wrangler.toml`.
4. **Verify.** Check the specific behavior, not only process activity. Worker changes require
   the authenticated session/security checks above. Bot changes require running status,
   restart count zero, clean startup logs, and the affected menu/API behavior.
5. **Commit.** Inspect `git status`, `git diff`, and recent history. Stage only requested files,
   run `git diff --cached --check`, and create a concise commit. Never amend unless requested.
6. **Push and CI.** Push the commit, find the matching Actions run by commit SHA, and watch it
   through Worker tests, multi-arch image build/push, and signing. Stop on failure.
7. **Production image.** Only after CI succeeds, restore the live compose service to
   `image: ghcr.io/freender/mwbot:main`, remove the temporary `build:` block, pull, and recreate
   `mwbot`.
8. **Final verification.** Confirm the container's OCI revision label equals the pushed commit,
   image source is GHCR, restart count is zero, startup logs are clean, Worker security checks
   pass when in scope, and the WAF remains ASN-only. A deploy that fails verification leaves
   `helm` diverged: stop before claiming success and report the observed state.

Report shipped scope, verification evidence, Worker version when changed, commit hash, Actions
URL/status, running image revision, and any skipped step.
