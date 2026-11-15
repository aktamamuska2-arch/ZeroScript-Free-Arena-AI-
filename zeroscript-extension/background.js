// SPDX-License-Identifier: GPL-3.0-or-later
// background.js - service worker.
// Owns ONE resilient WebSocket to the local bridge (ws://127.0.0.1:PORT).
// Keeping the socket here (not in the content script) avoids https→ws mixed
// content issues and centralises reconnect / timeout logic.
//
// Contract with content.js: every sendMessage ALWAYS gets a response object,
// even when the bridge is offline. The agentic loop must never hang waiting.

const PORT = 17613;
// BRIDGE_URL (not `URL`): a top-level `const URL` SHADOWS the global URL
// constructor for this whole worker - any later `new URL(...)` here would
// explode confusingly. Named for what it is instead.
const BRIDGE_URL = `ws://127.0.0.1:${PORT}`;
// Token endpoint on the bridge (same port). The bridge serves the per-install
// token ONLY to extension origins (Origin-gated, no CORS headers), so a web
// page can neither connect nor read the token; the service worker can, via the
// manifest's host_permissions for http://127.0.0.1/*.
const AUTH_URL = `http://127.0.0.1:${PORT}/zs-auth-token`;

// Chat sites where a ZeroScript provider content script runs. Status pushes go
// to every tab matching these. Add the new provider's URL pattern here (and in
// manifest.json content_scripts + host_permissions) when integrating another AI.
const PROVIDER_URLS = ["https://chat.deepseek.com/*", "https://chatgpt.com/*", "https://chat.openai.com/*", "https://gemini.google.com/*", "https://www.kimi.ai/*", "https://kimi.ai/*", "https://chat.z.ai/*", "https://chat.qwen.ai/*", "https://arena.ai/*", "https://www.meta.ai/*", "https://meta.ai/*"];

const RECONNECT_MIN = 1000;
const RECONNECT_MAX = 5000;
const HEARTBEAT_MS = 10000;
// If no message (incl. pong) arrives within this window while we believe we're
// connected, the socket is half-open: force a reconnect instead of letting
// pending requests slowly time out.
const STALE_SOCKET_MS = 25000;
const REQUEST_TIMEOUT_DEFAULT = 130000; // a bit above the 120s tool timeout

let ws = null;
let connected = false;
// Bridge auth state: pending (handshake running) | ok (token accepted) |
// legacy (old bridge without auth - still allowed) | failed (bridge refused
// the token and re-fetching one did not help). Surfaced in statusObj so the
// UI and diagnostics can tell these apart.
let authState = "pending";
let authToken = null; // in-memory only; re-fetched from the bridge on demand
let helloTimer = null;
let reconnectDelay = RECONNECT_MIN;
let reconnectTimer = null;
let heartbeatTimer = null;
let lastMessageAt = 0; // timestamp of the last frame received from the bridge
let nextId = 1;
const pending = new Map(); // id -> {resolve, timer}
let toolsCache = [];
let mcpAlive = false;
let serversCache = [];
// true/false = a PLACE is loaded and usable in Roblox Studio; null = unknown.
// The MCP process stays alive when Studio is closed or its MCP option is off,
// so this is probed separately (bridge "studio_status").
let studioConnected = null;
// true/false = a Roblox Studio app is connected to the MCP server at all; null =
// unknown. studioApp=true with studioConnected=false means "Studio open but no
// place"; studioApp=false means "Studio closed OR its MCP option disabled".
let studioApp = null;
// true/false = a Roblox Studio WINDOW/PROCESS exists on this machine (checked
// bridge-side via tasklist); null = unknown/old bridge. Distinguishes the two
// studioApp=false sub-cases the UI must word differently: Studio genuinely not
// launched ("open Roblox Studio") vs Studio OPEN but its MCP plugin never
// registered with the bridge - the documented fix for the latter is opening
// Assistant Settings > MCP Servers inside Studio (validated live 3x), which
// "open Roblox Studio" wording completely fails to convey.
let studioProc = null;

function log(...a) {
  console.log("[zs-bg]", ...a);
}

// ── WebSocket lifecycle ─────────────────────────────────────────────────
function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  clearTimeout(reconnectTimer);
  try {
    ws = new WebSocket(URL);
  } catch (e) {
    log("WebSocket ctor failed", e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    reconnectDelay = RECONNECT_MIN;
    lastMessageAt = Date.now();
    // NOT marking `connected` yet: the hello handshake must pass (or an old
    // bridge must reveal itself) before anything else is sent. send() waits
    // for `connected`, so no request can race the auth gate.
    doHello();
  };

  ws.onmessage = (ev) => {
    lastMessageAt = Date.now();
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    handleBridgeMessage(msg);
  };

  ws.onclose = () => {
    connected = false;
    authState = "pending"; // re-arm the handshake for the next attempt
    mcpAlive = false;
    studioConnected = null;
    studioApp = null;
    studioProc = null;
    serversCache = [];
    clearTimeout(helloTimer);
    stopHeartbeat();
    failAllPending("bridge connection closed");
    broadcastStatus();
    scheduleReconnect();
  };

  ws.onerror = () => {
    // onclose will follow; nothing to do here but avoid an unhandled error.
    try { ws.close(); } catch {}
  };
}

// ── hello handshake (bridge authentication) ───────────────────────────────
// Every connection sends {"type":"hello","token":...} as its FIRST message.
//  - New bridge accepts it -> "hello_ok" -> then the usual "connected" snapshot.
//  - New bridge rejects it -> close 4003 -> we re-fetch the token once (the
//    bridge may have rotated it) and reconnect.
//  - OLD bridge (pre-auth) replies to the unknown message type with an error
//    AND has already sent its "connected" snapshot first - either frame marks
//    the peer legacy and the session continues unauthenticated, exactly as it
//    did before this handshake existed.
async function fetchToken(timeoutMs = 4000) {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    const r = await fetch(AUTH_URL, { signal: ctrl.signal });
    clearTimeout(t);
    if (!r.ok) return null;
    const d = await r.json().catch(() => null);
    return d && typeof d.token === "string" && d.token ? d.token : null;
  } catch (e) {
    return null; // bridge down, or an old bridge without the endpoint
  }
}

async function doHello() {
  clearTimeout(helloTimer);
  helloTimer = setTimeout(() => {
    log("hello handshake timed out - dropping the socket");
    try { ws.close(); } catch {}
  }, 8000);
  if (authToken === null) authToken = await fetchToken();
  if (authToken === null) {
    // No endpoint answered: could be an old bridge (its "connected" frame will
    // still arrive) or a dead bridge (onclose fires). Send an empty-token
    // hello so BOTH cases resolve deterministically within the timeout.
    authToken = "";
  }
  try {
    ws.send(JSON.stringify({ type: "hello", token: authToken }));
  } catch (e) {
    log("hello send failed", e);
    try { ws.close(); } catch {}
  }
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 1.7, RECONNECT_MAX);
}

function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (connected) {
      // Half-open socket: the WS still reports OPEN but nothing comes through.
      // The pong (and every other frame) refreshes lastMessageAt; if it has
      // gone stale, drop the dead socket so onclose triggers a reconnect.
      if (lastMessageAt && Date.now() - lastMessageAt > STALE_SOCKET_MS) {
        log("socket stale, forcing reconnect");
        try { ws.close(); } catch {}
        return;
      }
      // Keeps the MV3 service worker alive AND detects a half-open socket.
      send({ type: "ping" }).catch(() => {});
      refreshStudioStatus();
    }
  }, HEARTBEAT_MS);
}

function stopHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

// Resolve once the socket is OPEN, or false after `timeout` ms.
function waitForConnection(timeout = 8000) {
  return new Promise((resolve) => {
    if (connected && ws && ws.readyState === WebSocket.OPEN) return resolve(true);
    connect(); // nudge a (re)connection - important after a worker wake-up
    const t0 = Date.now();
    const iv = setInterval(() => {
      if (connected && ws && ws.readyState === WebSocket.OPEN) {
        clearInterval(iv);
        resolve(true);
      } else if (Date.now() - t0 > timeout) {
        clearInterval(iv);
        resolve(false);
      }
    }, 100);
  });
}

// ── request/response over the socket ────────────────────────────────────
async function send(obj, timeout = REQUEST_TIMEOUT_DEFAULT) {
  // The MV3 service worker can be suspended; the first message after a wake-up
  // arrives before the socket has re-opened. Wait for it instead of failing -
  // otherwise Kimi wrongly hears "bridge offline".
  if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
    await waitForConnection(8000);
  }
  return new Promise((resolve) => {
    if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
      resolve({ ok: false, kind: "disconnected", error: "bridge not connected" });
      return;
    }
    const id = nextId++;
    const payload = { ...obj, id };
    const timer = setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        resolve({ ok: false, kind: "timeout", error: "bridge did not respond in time" });
      }
    }, timeout);
    pending.set(id, { resolve, timer });
    try {
      ws.send(JSON.stringify(payload));
    } catch (e) {
      clearTimeout(timer);
      pending.delete(id);
      resolve({ ok: false, kind: "disconnected", error: String(e) });
    }
  });
}

// Ask the bridge whether a Roblox Studio instance is actually connected to the
// MCP server. Broadcasts only on change so the UI updates promptly but quietly.
let studioProbing = false;
async function refreshStudioStatus() {
  if (studioProbing || !connected) return;
  studioProbing = true;
  try {
    const r = await send({ type: "studio_status" }, 12000);
    const v = r && r.ok && typeof r.studio === "boolean" ? r.studio : null;
    if (v !== studioConnected) {
      studioConnected = v;
      broadcastStatus();
    }
  } finally {
    studioProbing = false;
  }
}

function handleBridgeMessage(msg) {
  // ── auth handshake frames (before everything else) ──
  if (msg.type === "hello_ok") {
    clearTimeout(helloTimer);
    authState = "ok";
    connected = true;
    lastMessageAt = Date.now();
    log("bridge auth ok");
    startHeartbeat();
    broadcastStatus(); // the "connected" snapshot follows right after
    return;
  }
  if (msg.type === "hello_err") {
    clearTimeout(helloTimer);
    // Wrong token: the bridge may have rotated it. Re-fetch ONCE and retry;
    // if that also fails, surface "failed" but keep the reconnect loop alive
    // so a later rotation/install fix self-heals.
    (async () => {
      authToken = await fetchToken();
      if (authToken) {
        log("bridge refused the token - fetched a fresh one, reconnecting");
        try { ws.close(); } catch {}
      } else {
        log("bridge refused the token and no token endpoint answered");
        authState = "failed";
        connected = false;
        failAllPending("bridge auth failed");
        broadcastStatus();
        try { ws.close(); } catch {}
        scheduleReconnect();
      }
    })();
    return;
  }
  if (msg.type === "connected" && authState === "pending") {
    // The snapshot arrived BEFORE hello_ok -> the peer never gates on auth:
    // this is an OLD bridge. Mark legacy and carry on exactly as pre-auth
    // builds did (compat: new extension + old bridge).
    clearTimeout(helloTimer);
    authState = "legacy";
    connected = true;
    lastMessageAt = Date.now();
    log("old bridge without auth detected - continuing in legacy mode");
    startHeartbeat();
    broadcastStatus();
    // fall through to the normal "connected" handling below
  }
  if ("studio" in msg && (typeof msg.studio === "boolean" || msg.studio === null)) {
    studioConnected = msg.studio;
  }
  if ("studio_app" in msg && (typeof msg.studio_app === "boolean" || msg.studio_app === null)) {
    studioApp = msg.studio_app;
  }
  if ("studio_proc" in msg && (typeof msg.studio_proc === "boolean" || msg.studio_proc === null)) {
    studioProc = msg.studio_proc;
  }
  if (msg.type === "studio_status") {
    resolvePending(msg.id, { ok: true, studio: studioConnected });
    broadcastStatus();
    return;
  }
  if (msg.type === "connected") {
    mcpAlive = !!msg.mcp_alive;
    if (Array.isArray(msg.tools)) toolsCache = msg.tools;
    if (Array.isArray(msg.servers)) serversCache = msg.servers;
    broadcastStatus();
    return;
  }
  if (msg.type === "pong") {
    resolvePending(msg.id, { ok: true });
    return;
  }
  if (msg.type === "tools") {
    if (Array.isArray(msg.tools)) toolsCache = msg.tools;
    if (Array.isArray(msg.servers)) serversCache = msg.servers;
    mcpAlive = !!msg.mcp_alive;
    resolvePending(msg.id, { ok: true, tools: toolsCache });
    broadcastStatus();
    return;
  }
  if (msg.type === "tool_result") {
    resolvePending(msg.id, msg.ok
      ? { ok: true, text: msg.text, images: msg.images || [] }
      : { ok: false, kind: msg.kind, error: msg.error });
    return;
  }
  if (msg.type === "mcp_status") {
    mcpAlive = !!msg.alive;
    if (Array.isArray(msg.tools)) toolsCache = msg.tools;
    if (Array.isArray(msg.servers)) serversCache = msg.servers;
    resolvePending(msg.id, { ok: !!msg.ok, alive: msg.alive, error: msg.error });
    broadcastStatus();
    return;
  }
  if (msg.type === "server_changed") {
    // Live add/remove: the bridge no longer restarts itself (restarting:false),
    // so the socket stays up and a fresh "connected" broadcast follows once the
    // new server settles. restarting:true is still honoured for old bridges.
    resolvePending(msg.id, { ok: !!msg.ok, error: msg.error, warning: msg.warning, restarting: !!msg.restarting });
    return;
  }
  if (msg.type === "error") {
    resolvePending(msg.id, { ok: false, error: msg.error });
    return;
  }
}

function resolvePending(id, value) {
  const p = pending.get(id);
  if (!p) return;
  clearTimeout(p.timer);
  pending.delete(id);
  p.resolve(value);
}

function failAllPending(reason) {
  for (const [, p] of pending) {
    clearTimeout(p.timer);
    p.resolve({ ok: false, kind: "disconnected", error: reason });
  }
  pending.clear();
}

// ── status push to any open DeepSeek tab + popup ─────────────────────────
function statusObj() {
  return { type: "zs-status", connected, authState, mcpAlive, studio: studioConnected, studioApp, studioProc, tools: toolsCache.length, servers: serversCache };
}

function broadcastStatus() {
  chrome.runtime.sendMessage(statusObj()).catch(() => {});
  chrome.tabs.query({ url: PROVIDER_URLS }, (tabs) => {
    for (const t of tabs) chrome.tabs.sendMessage(t.id, statusObj()).catch(() => {});
  });
}

// ── messages from content.js / popup.js ─────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    switch (msg.type) {
      case "status":
        if (!connected) connect(); // self-heal after a worker wake-up
        sendResponse(statusObj());
        break;
      case "list_tools": {
        // Prefer a live refresh; fall back to cache so the loop never stalls.
        // 10s, not 25s: a catalogue request only blocks this long when one of the
        // MCP servers is dead (typically Roblox in a degraded, Blender-only
        // session), and in that exact case we already hold a perfectly good cached
        // catalogue. Waiting the full 25s just froze the boot for no new data.
        const r = await send({ type: "list_tools" }, 10000);
        if (r.ok) sendResponse({ ok: true, tools: r.tools });
        else sendResponse({ ok: toolsCache.length > 0, tools: toolsCache, error: r.error });
        break;
      }
      case "call_tool": {
        const timeout = (msg.timeout || 120000) + 10000;
        const r = await send(
          { type: "call_tool", name: msg.name, arguments: msg.arguments, timeout: msg.timeout },
          timeout
        );
        sendResponse(r);
        break;
      }
      case "restart_mcp": {
        const r = await send({ type: "restart_mcp" }, 30000);
        sendResponse(r);
        break;
      }
      case "add_server": {
        const r = await send({
          type: "add_server", server_id: msg.server_id,
          command: msg.command, args: msg.args, env: msg.env,
        }, 15000);
        sendResponse(r);
        break;
      }
      case "remove_server": {
        const r = await send({ type: "remove_server", server_id: msg.server_id }, 15000);
        sendResponse(r);
        break;
      }
      case "reconnect":
        reconnectDelay = RECONNECT_MIN;
        connect();
        sendResponse({ ok: true });
        break;
      default:
        sendResponse({ ok: false, error: "unknown message" });
    }
  })();
  return true; // async sendResponse
});

// Wake/keepalive hooks.
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

connect();
