// Sediment / PKE — MV3 service worker.
//
// Content scripts cannot reliably POST to http://127.0.0.1 from the
// page's origin (mixed-content blocking, CORS). The service worker is
// a privileged context: it sends the cross-origin POST to the local
// daemon, and buffers up to 200 events in `chrome.storage.local` when
// the daemon is unreachable. Buffered events are drained on the next
// successful send.
//
// Two message kinds arrive from the ISOLATED-world bridge:
//
//   * `pke_capture` — fire-and-forget evidence payload from the
//     conversation-turn observer. Sent to `/api/v1/evidence`; buffered
//     to `chrome.storage.local` when the daemon is unreachable.
//   * `pke_intervention` — request/response for the pre-Send Socratic
//     card. Forwarded synchronously to the requested `path`; the
//     reply body is returned to the caller (or `null` on any error or
//     timeout, so the MAIN-world card fails open).

const SERVER = "http://127.0.0.1:7421";
const EVIDENCE_URL = `${SERVER}/api/v1/evidence`;
const BUFFER_LIMIT = 200;
const INTERVENTION_TIMEOUT_MS = 1000;

async function bufferPayload(payload) {
  const existing = await chrome.storage.local.get({ pkeBuffer: [] });
  const next = existing.pkeBuffer.concat([payload]).slice(-BUFFER_LIMIT);
  await chrome.storage.local.set({ pkeBuffer: next });
}

async function send(payload) {
  // Content-Type: application/json keeps this a "simple" request from
  // the service-worker context and avoids a CORS preflight against the
  // local daemon.
  const resp = await fetch(EVIDENCE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
}

async function drain() {
  const existing = await chrome.storage.local.get({ pkeBuffer: [] });
  const remaining = [];
  for (const payload of existing.pkeBuffer) {
    try {
      await send(payload);
    } catch (_err) {
      remaining.push(payload);
    }
  }
  await chrome.storage.local.set({ pkeBuffer: remaining });
}

async function interventionRequest(path, body) {
  // Whitelist the two intervention paths we care about. Anything else
  // is dropped so the MAIN-world bridge can't be abused to hit
  // arbitrary daemon endpoints from a compromised page context.
  if (path !== "/api/v1/intervention/check" && path !== "/api/v1/intervention/outcome") {
    return null;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), INTERVENTION_TIMEOUT_MS);
  try {
    const resp = await fetch(`${SERVER}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
      signal: controller.signal,
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (_err) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message) return false;
  if (message.kind === "pke_capture") {
    send(message.payload)
      .then(drain)
      .catch(() => bufferPayload(message.payload));
    return false;
  }
  if (message.kind === "pke_intervention") {
    interventionRequest(message.path, message.body).then((body) => {
      sendResponse({ body });
    });
    // Returning true tells Chrome we'll call `sendResponse` async.
    return true;
  }
  return false;
});
