# Sediment / 沉淀

## Why this exists

It's good that AI takes over the repetitive parts of our work. Solving a quadratic by hand, writing yet another commit message, untangling a merge conflict — these are things you already know how to do, and delegating them frees up attention for harder problems or things you haven't seen before.

The same handoff is harmful in the other direction. When a student hits a concept they actually haven't learned and lets ChatGPT do the problem, the concept never goes in. The same thing happens to a working programmer with a tool they haven't touched in months or a mechanism they never really understood. Every time you let AI think for you on something that wasn't already practiced, the muscle quietly goes.

Sediment tries to split those two cases. While you work with AI, in real time or after the fact, it sorts the observed actions into ones you already have under control (let those pass through) and ones that are either new or that you've been quietly outsourcing for a while (worth doing once on your own). The first category sees no friction. The second category comes back later as a short practice item, at a moment a forgetting model thinks you'd benefit.

The hard part is not ingestion. Claude Code has hooks, ChatGPT and Claude.ai expose exports, browser extensions and HTTP proxies are well-understood patterns. The hard part is classifying and indexing the fuzzy thing called a "skill" so that the same underlying action shows up in the same place across very different sessions.

```bash
git clone https://github.com/pengw0048/sediment.git
cd sediment
uv sync                          # Python 3.11 or 3.12, plus uv
uv run pke init
uv run pke serve                 # http://127.0.0.1:7421
```

## Two modes

**Retrospective.** `pke daemon` watches your Claude Code transcripts and the `~/PKE/inbox/` drop folder, distills each session into named skills, and tracks them over time. Open `pke tui` or the web app whenever you have five minutes; the scheduler picks five items that look like they're slipping out of unaided reach, and an LLM writes the prompt on the spot — an explain-back, a variant, a Socratic first-step, or a straight replay.

**Real-time intervention (optional).** Before an AI call answers a question that touches a skill the scheduler thinks you're losing, an inline Socratic prompt appears first. Dismiss it with one keystroke and the AI continues. Five dismisses in a row on the same source auto-downgrade interventions for that source for 24 hours, so the feature doesn't become a nag.

```
pke serve          web UI
pke tui            terminal review
pke review         headless 5-item batch
pke daemon         tailers + nightly maintenance
pke export FILE    full backup
pke wipe           delete everything (config kept unless --purge-config)
```

## LLM (optional)

Sediment runs offline. Without an LLM the extraction layer falls back to deterministic heuristics and the judge degrades to self-report; you get coarser skills but the loop still closes. For the full pipeline, set exactly one of:

```bash
export ANTHROPIC_API_KEY=...                       # claude-haiku-4-5
export PKE_LLM_BASE_URL=http://localhost:8000/v1   # OpenAI-compatible endpoint
export OPENAI_API_KEY=...                          # gpt-5-mini
```

Every call lands in the `llm_call_log` table with provider, model, token counts, latency, and USD cost from the `PRICING` table. Anthropic is the default because Haiku 4.5 is cheap, supports prompt caching, and is strong enough as a judge.

```sql
SELECT SUM(cost_usd) FROM llm_call_log
WHERE called_at >= datetime('now', '-30 days');
```

## Where the data lives

| | |
|---|---|
| `~/.local/share/pke/evidence.db` | SQLite: evidence, skills, mastery, review answers, llm_call_log |
| `~/.local/share/pke/graph/` | Kuzu: skill nodes plus `parent_of` edges from the weekly Leiden run, bitemporal |
| `~/PKE/inbox/` | Drop ChatGPT / Claude.ai export `.json` here; the daemon imports them |
| `~/.config/pke/secret.toml` | API keys; `pke init` creates this `chmod 600` |

## What works, what doesn't

Working today: the Claude Code transcript tailer; the `~/PKE/inbox/` archive importer; the review loop through all three UIs (web / TUI / CLI); nightly decay, weekly Leiden, EDC merge sweep; cost-logged Anthropic / OpenAI / local-vLLM LLM endpoints.

Not working yet: the HTTP proxy adapters (OpenAI / Anthropic) start their servers but don't pass through SSE streams, so streaming chat isn't captured; the browser extension installs but isn't yet wired into the daemon's event stream.

Newly written, used by the author, not battle-tested.

## License

MIT.
