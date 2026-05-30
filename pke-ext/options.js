const enabled = document.getElementById("enabled");
chrome.storage.local.get({ enabled: true }).then((value) => {
  enabled.checked = value.enabled;
});
enabled.addEventListener("change", () => {
  chrome.storage.local.set({ enabled: enabled.checked });
});
