// Sediment / PKE — MV3 service worker.
//
// Content scripts cannot reliably POST to http://127.0.0.1 from the
// page's origin (mixed-content blocking, CORS). The service worker is
// a privileged context: it sends the cross-origin POST to the local
// daemon, and buffers up to 200 events in `chrome.storage.local` when
// the daemon is unreachable. Buffered events are drained on the next
// successful send.

const SERVER = "http://127.0.0.1:7421";
const EVIDENCE_URL = `${SERVER}/api/v1/evidence`;
const BUFFER_LIMIT = 200;

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

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.kind !== "pke_capture") return;
  send(message.payload)
    .then(drain)
    .catch(() => bufferPayload(message.payload));
});
