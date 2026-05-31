// Sediment / PKE — bridge from MAIN-world content script to the
// service worker. The MAIN world has access to the page's DOM/React
// state but not `chrome.runtime`; the ISOLATED world has the inverse.
// We connect them via `window.postMessage` carrying the `__pke__`
// marker.
window.addEventListener("message", (event) => {
  if (event.source !== window || !event.data || !event.data.__pke__) return;
  chrome.runtime.sendMessage({ kind: "pke_capture", payload: event.data.payload });
});
