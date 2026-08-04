# MWBot Network Check Worker

This Worker gives MWBot the client IP and ASN observed by Cloudflare. Sessions last five
minutes, are identified by a random 256-bit token, and contain no Telegram identifiers.
The browser-facing page never displays the detected values.

## Setup

1. Create a KV namespace:

   ```bash
   npx wrangler kv namespace create SESSIONS
   ```

2. Copy `wrangler.toml.example` to the ignored `wrangler.toml` and replace the KV namespace
   ID.

3. Generate a random API token and configure it as a Worker secret. Do not commit or paste
   the value into `wrangler.toml`:

   ```bash
   openssl rand -hex 32 | npx wrangler secret put API_TOKEN
   ```

4. Deploy the Worker:

   ```bash
   npx wrangler deploy
   ```

5. Attach a dedicated custom domain such as `access-check.example.com`. It must route only
   to the Worker, not to Traefik or another home-lab origin.

6. Set the same secret and Worker URL in MWBot's runtime environment:

   ```dotenv
   ACCESS_CHECK_API_URL=https://access-check.example.com
   ACCESS_CHECK_API_TOKEN=<same Worker API token>
   ```

## Request Flow

1. MWBot authenticates `POST /api/sessions` and receives a one-time check URL.
2. The user's browser opens `GET /check/<session>` from the network being authorized.
3. The Worker stores `CF-Connecting-IP` and `request.cf.asn` in KV.
4. MWBot authenticates `GET /api/sessions/<session>` and grants the exact IP or ASN in WAF.
5. After the WAF update succeeds, MWBot deletes the Worker session.

The API token protects session creation, retrieval, and deletion. The random check URL is a
short-lived bearer capability, so users should not share it. Workers KV can be eventually
consistent; MWBot keeps polling briefly before the menu reports the final result.

## Test

```bash
npm test
```
