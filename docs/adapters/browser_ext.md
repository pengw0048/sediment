# Browser Extension Adapter

## Install

Load `pke-ext/` as an unpacked Manifest V3 extension.

## Configuration

The extension captures ChatGPT, Claude.ai, and Gemini requests and posts to
`http://127.0.0.1:7421/api/v1/evidence`.

## Failure Modes

- Local server unavailable: payloads are buffered in `chrome.storage.local`.
- Site schema changes: the extension emits partial events where possible.

## Debug

Open the extension popup and check server reachability.
