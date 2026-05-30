const SERVER = "http://127.0.0.1:7421";

async function bufferPayload(payload) {
  const existing = await chrome.storage.local.get({ pkeBuffer: [] });
  const next = existing.pkeBuffer.concat([payload]).slice(-200);
  await chrome.storage.local.set({ pkeBuffer: next });
}

async function drain() {
  const existing = await chrome.storage.local.get({ pkeBuffer: [] });
  const remaining = [];
  for (const payload of existing.pkeBuffer) {
    try {
      await fetch(`${SERVER}/api/v1/evidence`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch (err) {
      remaining.push(payload);
    }
  }
  await chrome.storage.local.set({ pkeBuffer: remaining });
}

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.kind !== "pke_capture") return;
  fetch(`${SERVER}/api/v1/evidence`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message.payload)
  }).then(drain).catch(() => bufferPayload(message.payload));
});
