const SESSION_TTL_SECONDS = 5 * 60;
const SESSION_ID_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const AS_NUMBER_MAX = 4_294_967_295;
const CHECK_COMPLETE_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Network check complete</title>
  <style>
    :root { color-scheme: dark; font-family: ui-rounded, "SF Pro Rounded", system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100svh; display: grid; place-items: center; padding: 24px; background: radial-gradient(circle at top, #1e293b 0, #090d16 58%); color: #e7edf7; }
    main { width: min(100%, 420px); padding: 38px 30px; border: 1px solid #334155; border-radius: 24px; background: #111827; box-shadow: 0 24px 70px #0009; text-align: center; }
    .status { width: 64px; height: 64px; display: grid; place-items: center; margin: 0 auto 22px; border-radius: 50%; background: #123524; color: #6ee7a8; font-size: 34px; font-weight: 800; }
    .spinner { width: 32px; height: 32px; border: 4px solid #7dd3fc44; border-top-color: #7dd3fc; border-radius: 50%; animation: spin .6s linear infinite; }
    .eyebrow { margin: 0 0 8px; color: #7dd3fc; font-size: 12px; font-weight: 800; letter-spacing: .18em; }
    h1 { margin: 0; font-size: clamp(28px, 8vw, 38px); line-height: 1.08; }
    .message { margin: 18px 0 0; color: #aebbd0; font-size: 17px; line-height: 1.55; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
  </style>
</head>
<body>
  <main>
    <div class="status" aria-hidden="true"><div class="spinner"></div></div>
    <p class="eyebrow">MWBOT</p>
    <h1 id="title">Updating Telegram</h1>
    <p class="message" id="message">Keep this page open for a moment while MWBot applies access.</p>
  </main>
  <script>
    setTimeout(() => {
      document.querySelector(".status").innerHTML = "&#10003;";
      document.querySelector("#title").textContent = "Network detected";
      document.querySelector("#message").textContent = "Return to Telegram to see the result.";
    }, 3000);
  </script>
</body>
</html>`;

function securityHeaders(contentType) {
  return {
    "Cache-Control": "no-store, max-age=0",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "Content-Type": contentType,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: securityHeaders("application/json; charset=utf-8") });
}

function error(message, status) {
  return json({ error: message }, status);
}

function remainingSeconds(expiresAt, now = Date.now()) {
  return Math.max(0, Math.ceil((expiresAt - now) / 1000));
}

function isSessionId(value) {
  return SESSION_ID_PATTERN.test(value);
}

function isAsn(value) {
  return Number.isInteger(value) && value > 0 && value <= AS_NUMBER_MAX;
}

function newSessionId() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function isAuthorized(request, env) {
  return typeof env.API_TOKEN === "string" && env.API_TOKEN.length > 0 && request.headers.get("Authorization") === `Bearer ${env.API_TOKEN}`;
}

async function getSession(env, id) {
  const session = await env.SESSIONS.get(id, "json");
  if (!session || typeof session !== "object" || !Number.isFinite(session.expiresAt)) return null;
  if (session.expiresAt <= Date.now()) {
    await env.SESSIONS.delete(id);
    return null;
  }
  return session;
}

function sessionResponse(id, session) {
  const body = { id, status: session.status, expires_in: remainingSeconds(session.expiresAt) };
  if (session.status === "complete") {
    body.asn = session.asn;
    if (session.asOrganization) body.as_organization = session.asOrganization;
  }
  return json(body);
}

async function createSession(request, env, url) {
  if (!isAuthorized(request, env)) return error("unauthorized", 401);

  const now = Date.now();
  const id = newSessionId();
  const expiresAt = now + SESSION_TTL_SECONDS * 1000;
  await env.SESSIONS.put(id, JSON.stringify({ status: "pending", expiresAt }), { expirationTtl: SESSION_TTL_SECONDS });
  return json({ id, check_url: `${url.origin}/check/${id}`, expires_in: SESSION_TTL_SECONDS }, 201);
}

async function checkSession(request, env, id) {
  if (!isSessionId(id)) return error("not found", 404);
  const session = await getSession(env, id);
  if (!session) return error("not found", 404);
  if (session.status === "complete") return checkCompleteResponse();
  if (session.status !== "pending") return error("not found", 404);

  const asn = requestCfAsn(request);
  if (!isAsn(asn)) return error("network data unavailable", 422);

  const ttl = remainingSeconds(session.expiresAt);
  if (ttl === 0) {
    await env.SESSIONS.delete(id);
    return error("not found", 404);
  }
  // KV requires at least 60 seconds; expiresAt still enforces the original session deadline.
  const asOrganization = requestCfAsOrganization(request);
  const completeSession = { status: "complete", expiresAt: session.expiresAt, asn };
  if (asOrganization) completeSession.asOrganization = asOrganization;
  await env.SESSIONS.put(id, JSON.stringify(completeSession), { expirationTtl: Math.max(60, ttl) });
  return checkCompleteResponse();
}

function checkCompleteResponse() {
  return new Response(CHECK_COMPLETE_HTML, { headers: securityHeaders("text/html; charset=utf-8") });
}

function requestCfAsn(request) {
  return request.cf?.asn;
}

function requestCfAsOrganization(request) {
  const organization = request.cf?.asOrganization;
  if (typeof organization !== "string") return null;
  const normalized = organization.trim();
  return normalized && normalized.length <= 120 ? normalized : null;
}

async function apiSession(request, env, id) {
  if (!isAuthorized(request, env)) return error("unauthorized", 401);
  if (!isSessionId(id)) return error("not found", 404);
  const session = await getSession(env, id);
  if (!session || !["pending", "complete"].includes(session.status)) return error("not found", 404);
  if (request.method === "GET") return sessionResponse(id, session);
  if (request.method === "DELETE") {
    await env.SESSIONS.delete(id);
    return new Response(null, { status: 204, headers: securityHeaders("text/plain; charset=utf-8") });
  }
  return error("method not allowed", 405);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/sessions" && request.method === "POST") return createSession(request, env, url);
    const check = url.pathname.match(/^\/check\/([^/]+)$/);
    if (check && request.method === "GET") return checkSession(request, env, check[1]);
    const api = url.pathname.match(/^\/api\/sessions\/([^/]+)$/);
    if (api) return apiSession(request, env, api[1]);
    return error("not found", 404);
  },
};
