# Sediment

*Close the loop on what AI did for you.*

Sediment is a local-first Personal Knowledge Engine. It observes your own AI
interactions, stores them on your machine, extracts the skills involved, and
brings them back later as short review sessions so AI-assisted work can become
actual unaided ability.

- Local-first: SQLite, Kuzu-compatible graph storage, and local files under your
  data directory.
- No telemetry: there is no phone-home path, anonymous ping, hosted edition, or
  data sale.
- Open source: everything in this repository is MIT-licensed.
- Non-blocking: Sediment never rewrites AI responses and never gates the answer
  behind a lesson.

## Quick Start

```bash
pipx install pke
pke init
pke serve
```

The web app runs at `http://127.0.0.1:7421`.

## What This Is Not

Sediment is not a SaaS, not a parent dashboard, not an AI dependency score, and
not a blocker that refuses to let AI answer. It is a quiet review loop for your
own machine.
