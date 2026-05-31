# PKE Browser Extension

A Chrome MV3 extension that captures your ChatGPT conversations and
forwards them to the local Sediment daemon at
`http://127.0.0.1:7421/api/v1/evidence`.

## Status

Phase 1 (this version): **ChatGPT evidence capture only**. The extension
attaches a `MutationObserver` to the conversation DOM and ships each
completed user/assistant pair to the daemon.

Out of scope for this phase:

- Pre-Send "Socratic card" intervention. The hook is stubbed in
  `content-main.js` — see the `PRE_SEND_INTERVENTION` TODO — and is
  blocked on a server-side "draft prompt to skill id" helper.
- Claude.ai and Gemini capture. The previous network-interception
  paths in this folder targeted those sites too; they are deferred to
  later phases.

## Load the unpacked extension

1. Make sure the Sediment daemon is running locally and listening on
   `127.0.0.1:7421`. From the repo root:

   ```bash
   uv run pke serve
   ```

   Confirm it's up:

   ```bash
   curl -s http://127.0.0.1:7421/api/v1/extension/status
   # -> {"server":"reachable"}
   ```

2. Open `chrome://extensions` in Chrome (or any Chromium-based
   browser at version 120+).

3. Toggle **Developer mode** on (top right).

4. Click **Load unpacked** and select this `pke-ext/` directory.

5. Pin the PKE icon from the toolbar's puzzle-piece menu for quick
   reachability checks. Clicking the icon shows a popup that probes
   `GET /api/v1/extension/status` so you can verify the daemon is
   reachable.

## Confirm it shows up in the evidence stream

1. Open <https://chatgpt.com/>.
2. Send any prompt and wait for ChatGPT's reply to finish streaming.
3. Within a couple of seconds, the extension posts the pair to
   `POST /api/v1/evidence` with `source: "browser_ext_chatgpt"`. Check
   the daemon log for a 200 response, or query the evidence store:

   ```bash
   uv run pke evidence list --source browser_ext_chatgpt --limit 5
   ```

   You should see your turn with role `user`, the assistant text
   below it, and the conversation id pulled from the URL
   (`/c/<uuid>`).

## How it works

- `manifest.json` — MV3 manifest. Two content scripts run at
  `document_idle`: `content-main.js` in the MAIN world and
  `content-isolated.js` in the ISOLATED world. Host permissions are
  scoped to `chat.openai.com`, `chatgpt.com`, and the local daemon.
- `content-main.js` — MutationObserver over the conversation DOM.
  Detects user turns by `[data-message-author-role="user"]` and
  assistant turns by `[data-message-author-role="assistant"]`. Pairs
  each user turn with the next completed assistant turn (signaled by
  `data-message-streaming` clearing, with a 1.5s stability backstop)
  and posts the pair via `window.postMessage`.
- `content-isolated.js` — bridge from MAIN to the service worker via
  `chrome.runtime.sendMessage`.
- `background.js` — service worker. Sends the payload to the daemon;
  on failure buffers up to 200 events in `chrome.storage.local` and
  retries on the next successful send.
- `popup.{html,js,css}` — toolbar popup, shows daemon reachability
  and a strength-level setting that is reserved for the future
  intervention path (currently no-op).

## Payload shape

The service worker POSTs JSON of the form:

```json
{
  "source": "browser_ext_chatgpt",
  "conversation_id": "01940c0c-...",
  "turn_index": 0,
  "user_message_id": "aaa-...",
  "assistant_message_id": "bbb-...",
  "user_text": "...",
  "assistant_text": "...",
  "t0": 1717024000000,
  "url": "https://chatgpt.com/c/01940c0c-..."
}
```

The server endpoint
(`pke/adapters/browser_ext_endpoint.py::event_from_browser_payload`)
also accepts the legacy `reqBody`/`body` shape for backward
compatibility with older content scripts and for the integration
tests.
