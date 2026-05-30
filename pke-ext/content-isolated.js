window.addEventListener("message", (event) => {
  if (event.source !== window || !event.data || !event.data.__pke__) return;
  chrome.runtime.sendMessage({ kind: "pke_capture", payload: event.data.payload });
});
