import assert from "node:assert/strict";
import test from "node:test";
import worker from "../src/index.js";

class MockKv {
  constructor() { this.values = new Map(); this.lastPutOptions = null; }
  async get(key, type) { const value = this.values.get(key); return value && type === "json" ? JSON.parse(value) : value ?? null; }
  async put(key, value, options) { this.values.set(key, value); this.lastPutOptions = options; }
  async delete(key) { this.values.delete(key); }
}

function env() { return { API_TOKEN: "test-token", SESSIONS: new MockKv() }; }
function request(path, options = {}) {
  const { cf, ...requestOptions } = options;
  const value = new Request(`https://worker.example${path}`, requestOptions);
  if (cf) Object.defineProperty(value, "cf", { value: cf });
  return value;
}
function auth(method = "GET") { return { method, headers: { Authorization: "Bearer test-token" } }; }

test("creates, records, retrieves, and consumes a session", async () => {
  const bindings = env();
  const created = await worker.fetch(request("/api/sessions", auth("POST")), bindings);
  assert.equal(created.status, 201);
  assert.equal(created.headers.get("Cache-Control"), "no-store, max-age=0");
  const session = await created.json();
  assert.match(session.id, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(session.check_url, `https://worker.example/check/${session.id}`);

  const checked = await worker.fetch(request(`/check/${session.id}`, { cf: { asn: 64512 } }), bindings);
  assert.equal(checked.status, 200);
  const checkedText = await checked.text();
  assert.doesNotMatch(checkedText, /64512/);
  assert.match(checkedText, /apply access automatically/);
  assert.match(checkedText, /color-scheme: dark/);
  assert.match(checkedText, /background: #111827/);
  assert.match(checked.headers.get("Content-Security-Policy"), /style-src 'unsafe-inline'/);

  const complete = await worker.fetch(request(`/api/sessions/${session.id}`, auth()), bindings);
  const completePayload = await complete.json();
  assert.deepEqual(completePayload, { id: session.id, status: "complete", expires_in: 300, asn: 64512 });
  assert.equal("ip" in completePayload, false);
  const deleted = await worker.fetch(request(`/api/sessions/${session.id}`, auth("DELETE")), bindings);
  assert.equal(deleted.status, 204);
  assert.equal((await worker.fetch(request(`/api/sessions/${session.id}`, auth()), bindings)).status, 404);
});

test("rejects unauthenticated API requests and invalid observed values", async () => {
  const bindings = env();
  assert.equal((await worker.fetch(request("/api/sessions", { method: "POST" }), bindings)).status, 401);
  const created = await worker.fetch(request("/api/sessions", auth("POST")), bindings);
  const { id } = await created.json();
  const checked = await worker.fetch(request(`/check/${id}`, { cf: { asn: 0 } }), bindings);
  assert.equal(checked.status, 422);
  const pending = await worker.fetch(request(`/api/sessions/${id}`, auth()), bindings);
  assert.equal((await pending.json()).status, "pending");
});

test("rejects invalid IDs and handles repeated checks", async () => {
  const bindings = env();
  assert.equal((await worker.fetch(request("/check/bad"), bindings)).status, 404);
  const created = await worker.fetch(request("/api/sessions", auth("POST")), bindings);
  const { id } = await created.json();
  const options = { cf: { asn: 64496 } };
  assert.equal((await worker.fetch(request(`/check/${id}`, options), bindings)).status, 200);
  assert.equal((await worker.fetch(request(`/check/${id}`, options), bindings)).status, 200);
});

test("uses KV minimum TTL during the final session minute", async () => {
  const bindings = env();
  const created = await worker.fetch(request("/api/sessions", auth("POST")), bindings);
  const { id } = await created.json();
  const session = JSON.parse(bindings.SESSIONS.values.get(id));
  session.expiresAt = Date.now() + 30_000;
  bindings.SESSIONS.values.set(id, JSON.stringify(session));

  const options = { cf: { asn: 64496 } };
  assert.equal((await worker.fetch(request(`/check/${id}`, options), bindings)).status, 200);
  assert.equal(bindings.SESSIONS.lastPutOptions.expirationTtl, 60);
});
