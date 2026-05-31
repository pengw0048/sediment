// Sediment / PKE — bridge from MAIN-world content script to the
// service worker. The MAIN world has access to the page's DOM/React
// state but not `chrome.runtime`; the ISOLATED world has the inverse.
//
// Two channels travel over `window.postMessage`:
//
//   * `__pke__` — fire-and-forget capture payloads (conversation
//     turns). Forwarded to `background.js` as a `pke_capture`
//     message; no response is expected by the sender.
//   * `__pke_req__` — request/response bridge for the pre-Send
//     intervention card. The MAIN world sends `{id, path, body}`; we
//     call `chrome.runtime.sendMessage({kind: "pke_intervention", ...})`
//     and post `{__pke_resp__: true, id, body}` back on the same
//     window so MAIN can resolve the awaiting promise. Timeouts are
//     enforced by the MAIN-world caller; if `background.js` never
//     replies we simply drop the message.
//   * `__pke_log__` — fire-and-forget diagnostic events from the dead-DOM
//     watchdog. Forwarded to `background.js` as `pke_log` so service
//     workers can persist or surface selector breakage without us
//     touching the host page's console.
window.addEventListener("message", (event) => {
  if (event.source !== window || !event.data) return;
  if (event.data.__pke__) {
    chrome.runtime.sendMessage({ kind: "pke_capture", payload: event.data.payload });
    return;
  }
  if (event.data.__pke_log__) {
    const { level, event: eventName, ...rest } = event.data;
    chrome.runtime.sendMessage({
      kind: "pke_log",
      level: level || "info",
      event: eventName,
      detail: rest,
    });
    return;
  }
  if (event.data.__pke_req__) {
    const { id, path, body } = event.data;
    chrome.runtime.sendMessage(
      { kind: "pke_intervention", id, path, body },
      (response) => {
        // `chrome.runtime.lastError` fires when the service worker is
        // asleep or the port closed early; in either case we send a
        // null body so the MAIN-world side can fall open.
        const replyBody =
          chrome.runtime.lastError || !response ? null : response.body;
        window.postMessage(
          { __pke_resp__: true, id, body: replyBody },
          window.location.origin,
        );
      },
    );
  }
});
