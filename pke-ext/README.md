# PKE Browser Extension

A Chrome MV3 extension that captures your ChatGPT, Claude.ai, and
Gemini conversations and forwards them to the local Sediment daemon at
`http://127.0.0.1:7421/api/v1/evidence`.

## Status

Phase 1 of multi-site capture: **ChatGPT, Claude.ai, and Gemini
evidence capture**. The extension attaches a `MutationObserver` to
each supported site's conversation DOM and ships each completed
user/assistant pair to the daemon.

Per-site stability:

- **ChatGPT** (`chat.openai.com`, `chatgpt.com`) — first-class
  support. Stable `data-message-author-role` / `data-message-id` /
  `data-message-streaming` attributes. This is the contract the
  observer was originally built against.
- **Claude.ai** — **best effort**. Anthropic does not publish a
  durable DOM contract, and the underlying class names churn often.
  We key off a small bag of `data-testid` selectors with fallbacks;
  expect breakage and selector updates as the DOM evolves.
- **Gemini** (`gemini.google.com`) — **best effort**. Angular custom
  elements inside shadow DOM; no streaming attribute, so completion
  detection relies on a longer stability timer. Message ids are
  synthesized from a short content hash.

Out of scope for this phase:

- Pre-Send "Socratic card" intervention. The hook is stubbed in
  `content-main.js` — see the `PRE_SEND_INTERVENTION` TODO — and is
  blocked on a server-side "draft prompt to skill id" helper.

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

1. Open one of the supported sites (<https://chatgpt.com/>,
   <https://claude.ai/>, or <https://gemini.google.com/>).
2. Send any prompt and wait for the assistant's reply to finish
   streaming.
3. Within a couple of seconds, the extension posts the pair to
   `POST /api/v1/evidence` with the matching source name. Check the
   daemon log for a 200 response, or query the evidence store:

   ```bash
   uv run pke evidence list --source browser_ext_chatgpt --limit 5
   uv run pke evidence list --source browser_ext_claude_ai --limit 5
   uv run pke evidence list --source browser_ext_gemini --limit 5
   ```

   You should see your turn with role `user`, the assistant text
   below it, and a conversation id derived from the URL (or
   synthesized for first-message landing pages where the URL has no
   conversation segment yet).

## How it works

- `manifest.json` — MV3 manifest. Two content scripts run at
  `document_idle`: `content-main.js` in the MAIN world and
  `content-isolated.js` in the ISOLATED world. Host permissions are
  scoped to `chat.openai.com`, `chatgpt.com`, `claude.ai`,
  `gemini.google.com`, and the local daemon. The two `matches` lists
  in the manifest must stay identical — the MAIN/ISOLATED bridge only
  works when both worlds load on the same pages.
- `content-main.js` — site-aware MutationObserver over the
  conversation DOM. A `SITE_PROFILES` table at the top of the file is
  the single source of truth for per-site selectors and streaming
  attributes; updating a selector is a one-line edit. The observer
  pairs each user turn with the next completed assistant turn
  (signaled by the site's streaming attribute clearing, or a per-site
  stability timer) and posts the pair via `window.postMessage`.
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

`source` is one of `browser_ext_chatgpt`, `browser_ext_claude_ai`, or
`browser_ext_gemini`. The server endpoint
(`pke/adapters/browser_ext_endpoint.py::event_from_browser_payload`)
also accepts the legacy `reqBody`/`body` shape for backward
compatibility with older content scripts and for the integration
tests, and falls back to a URL-derived source when the payload's
`source` is missing or unknown.
