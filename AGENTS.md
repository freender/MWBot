# MWBot Repo Notes

- Entry point: `src/main.py`
- Bot pattern: Telegram handlers in `src/main.py`, service logic in `src/modules/`, env config in `src/cfg.py`
- Tests: `python -m unittest tests.test_modules tests.test_main`
- Prefer explicit error handling and focused unit tests for helper flows
- Keep user-facing Telegram replies short and actionable
- Maintenance flow: use `/start` as the menu-only entry point; the owner-only Alertmanager MW menu owns its silence and persisted state
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

MWBot is the **producer** for an incident pipeline whose consumer lives in another repository.
`src/modules/incidents.py` opens an issue in `GITHUB_INCIDENT_REPO` and then posts a comment
that triggers a triage workflow **in that repo**. Three things are load-bearing across the
boundary:

- **Repo.** `GITHUB_INCIDENT_REPO` (`src/cfg.py`) defaults to `freender/homelab-ops`. The
  triage workflow exists only there; pointing this elsewhere files issues nothing consumes.
- **Trigger token.** `TRIAGE_TRIGGER_COMMENT` must begin with `/oc`. The workflow matches that
  token in the comment body. The rest of the sentence is prompt context and may be reworded;
  the leading token may not.
- **Comment identity.** `GITHUB_INCIDENT_TOKEN` must belong to the repository owner. The
  workflow also gates on the commenting actor being the owner, so a GitHub App or bot token
  would post the comment successfully and triage would silently never run.

Breaking any of these produces **no local signal** — the issue is still filed, the comment
still posts, and the failure is a report that never appears in a repo this suite cannot see.
`tests/test_modules.py` covers the trigger token only; the repo default and the token identity
are not testable from here. Change them deliberately.

On the authoring host, `opencode.json` references the consuming repo as `homelab-ops` for
read access. That repo is private: do not copy its workflow internals, runner details, or
infrastructure notes into this public repository.

## Validation

Run before every local deploy and again before committing. The suite imports `telebot`, which
is only present in the virtualenv, so activate it first — a bare `python3` fails every test
with `ModuleNotFoundError: No module named 'telebot'` rather than reporting a real problem:

```bash
source .venv/bin/activate
python3 -m py_compile src/main.py src/cfg.py src/modules/__init__.py \
  src/modules/common.py src/modules/firewall.py src/modules/maintenance.py \
  src/modules/incidents.py src/modules/network_check.py src/modules/redownload.py \
  tests/test_main.py tests/test_modules.py
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
