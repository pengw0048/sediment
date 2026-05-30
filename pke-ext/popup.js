fetch("http://127.0.0.1:7421/api/v1/extension/status")
  .then(() => {
    document.getElementById("status").textContent = "PKE server: reachable";
  })
  .catch(() => {
    document.getElementById("status").textContent = "PKE server: not reachable";
  });

document.getElementById("strength").addEventListener("change", (event) => {
  chrome.storage.local.set({ strength: event.target.value });
});
