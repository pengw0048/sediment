# Claude Code Adapter

## Install

```bash
pke install-hook claude-code
```

## Configuration

The installer merges `UserPromptSubmit` and `PostToolUse` hooks into
`~/.claude/settings.json` and writes a timestamped backup.

## Failure Modes

- PKE offline: the hook buffers envelopes under `~/.pke/hook_buffer`.
- Settings file not writable: the command exits with an error.
- Unknown payload fields: fields are preserved in metadata where possible.

## Debug

```bash
pke doctor
pke evidence list --source claude_code_hook
```
