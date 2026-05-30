# Sediment / 沉淀

> Watch your AI sessions on-device, surface the skills that are slipping, replay them as short practice.

```bash
git clone https://github.com/pengw0048/sediment.git
cd sediment
uv sync
uv run pke init
uv run pke serve     # http://127.0.0.1:7421
```

Python 3.11 or 3.12, plus [`uv`](https://github.com/astral-sh/uv). Nothing else.

## Commands

| | |
|---|---|
| `pke serve` | Web app: review, browse skills, browse evidence |
| `pke tui` | Terminal review (predict → answer → grade) |
| `pke review` | Non-interactive 5-item batch |
| `pke daemon` | Watchdog tailers, nightly decay, EDC merge sweep, weekly Leiden |
| `pke export out.tar.gz` | Full backup of the data directory |
| `pke wipe` | Delete everything (config kept by default) |

## 沉淀是什么

跟 Claude Code 写了几个月代码之后，我注意到一件事：同一类问题被 AI 帮我解决过几十次，但我自己再写还是写不出来。沉淀就是把这个落差记录下来，然后让我有空的时候自己再做一遍。

跑法是这样的：

1. Adapter 监听本机的 AI 活动。目前真接进 daemon 的两个是 Claude Code 的 transcript tailer（实时跟 `~/.claude/transcripts/*.jsonl`）和 inbox watcher（往 `~/PKE/inbox/` 扔 ChatGPT/Claude.ai 的导出包就自动 import）。
2. 一次 LLM 调用把每段对话蒸馏成具名的"技能"。technique-level 的颗粒度，不是"Python"这种泛的。
3. 每个技能维护两个独立数字：用 AI 帮的时候你能做到什么程度、不靠 AI 自己能做到什么程度。两个数独立调度。
4. 当"不靠 AI"那个数的 HLR 预测概率掉下去，scheduler 把它排进今天的 5 题。
5. Generator 写题，你答，judge 打分（symbolic / code-exec / self-report / LLM-judge 四种 grader），状态机更新。

数据全在本机。没有云端版本、不发遥测、不需要注册。

## LLM (optional)

Sediment runs offline; extraction and judge degrade to deterministic fallbacks. To turn on real LLM extraction, set exactly one of:

```bash
export ANTHROPIC_API_KEY=...                       # → claude-haiku-4-5
export PKE_LLM_BASE_URL=http://localhost:8000/v1   # → any OpenAI-compatible endpoint
export OPENAI_API_KEY=...                          # → gpt-5-mini
```

每次 LLM 调用进 `llm_call_log` 表，记 token、延迟、按 PRICING 表算的美元成本。`SELECT SUM(cost_usd) FROM llm_call_log WHERE called_at >= datetime('now', '-30 days')` 就能看月成本。Anthropic / OpenAI 走 prompt cache。

## Where the data lives

| Path | Contents |
|---|---|
| `~/.local/share/pke/evidence.db` | SQLite: evidence, skills, mastery, review answers, llm_call_log |
| `~/.local/share/pke/graph/` | Kuzu: skill hierarchy, bitemporal `parent_of` edges from weekly Leiden |
| `~/.local/share/pke/tailer_offsets.json` | Per-file resume cursors |
| `~/PKE/inbox/` | Drop archives here; daemon imports + moves to `processed/` |
| `~/.config/pke/config.toml` | Adapter toggles, thresholds, model picks |
| `~/.config/pke/secret.toml` | API keys; created `chmod 600` by `pke init` |

## Stack

SQLite + Kuzu for storage. Half-Life Regression (offline `fit()` via scipy L-BFGS-B) plus FSRS-4.5 for scheduling. HNSW (hnswlib, M=32, ef=200) plus DenStream (river.cluster) for online identity. igraph Leiden for the weekly hierarchy job. APScheduler for the cron tier. FastAPI + Textual on the read side. Anthropic Messages or OpenAI Chat Completions on the write side, with prompt caching where the provider supports it.

## Status

23 merged PRs, 143 tests, 6 SQLite migrations. The intervention engine, EDC nightly merge, FSRS scheduling, real-time tailer, drift metrics, and the Leiden → spreading pipeline all run. Browser extension and HTTP proxy adapters are scaffolded but not yet wired into the daemon (see `pke/adapters/registry.py` — only `ACTIVE_PRODUCERS` get started).

This is a personal project. Issues welcome; I'm not promising long-term maintenance.

## License

MIT.
