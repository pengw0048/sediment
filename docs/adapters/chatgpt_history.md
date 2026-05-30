# ChatGPT History Import

## Install

No installation is required.

## Usage

```bash
pke import chatgpt ~/Downloads/chatgpt-export.zip
```

## Failure Modes

Damaged archives fail fast. Individual malformed conversations are skipped by
the importer tests and reported by command output.
