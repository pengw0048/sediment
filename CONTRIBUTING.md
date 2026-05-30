# Contributing

Thanks for working on Sediment.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy pke/
uv run pytest
```

Commits should be small, written in English, and describe the completed change.

## Adapters

Adapters emit `EvidenceEvent` objects only. They do not write directly to
storage, perform redaction, or decide identity. Add docs under
`docs/adapters/<name>.md` and tests for a happy path plus one error path.

## Pull Requests

Include what changed, how it was tested, and any local-only privacy impact. PKE
does not accept telemetry, SaaS paths, phone-home checks, or payment features.

Use DCO sign-off with `git commit -s`.
