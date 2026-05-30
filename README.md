# 沉淀 / Sediment

去年开始我用 Claude Code 干日常的活儿。一个我没用过的工具、一门我看着别扭的语言，开口几句话就能让 AI 帮我跑起来。这种"加速"是真实的。但用了几个月之后我注意到一件让人不太舒服的事实：让我离开 Claude，自己徒手写同样的东西，那种本应有的"上次明明做过"的肌肉记忆，是没有的。

不是没有补救办法。可以记笔记、画思维导图、买 Anki 卡片包。但所有这些都要求我先意识到"我刚才学到了什么"，再花时间整理出来。而恰恰是用 AI 的时候，我最不在意的就是这件事。我盯着要做的活，不是要学的东西。

沉淀想把这一步自动化。

它在本机跑一个小后台，安静地听你跟 AI 之间来回的每一句话，从里面抽出技能。不是泛泛的"Kubernetes"或者"FastAPI"，而是颗粒度更细的、技术动作层面的东西：怎么用 `kubectl describe` 看 pod 的退出码、FastAPI 的 dependency 怎么让它每次请求都重跑、Cypher 怎么写 variable-length path。每个技能维护两个数：用 AI 的时候你能做到什么程度，不靠 AI 自己能做到什么程度。两个数独立调度，谁滑下去谁就在下一次复习里被推到你面前。

没有云端，没有遥测，没有账号。

```bash
git clone https://github.com/pengw0048/sediment.git
cd sediment
uv sync
uv run pke init
uv run pke serve     # http://127.0.0.1:7421
```

需要 Python 3.11 / 3.12 + [uv](https://github.com/astral-sh/uv)，没了。

## How it actually flows

The smallest end-to-end loop: leave `pke daemon` running so the Claude Code transcript tailer and the `~/PKE/inbox/` watcher can ingest in real time, then check in via `pke tui` or the web app whenever you've got five minutes. Each session is five items, chosen by a selector that mixes forgetting curve, mastery gap, recent AI-assist pressure, and novelty. Items are generated on the spot by an LLM, in one of four flavors — replay-self-try, Socratic-first-step, variant, or explain-back — and graded right after you answer.

| | |
|---|---|
| `pke serve` | Web app — review, browse skills, browse evidence |
| `pke tui` | Terminal review (predict → answer → grade) |
| `pke review` | Headless five-item batch, useful for cron / scripts |
| `pke daemon` | Real-time tailers plus nightly decay, EDC merge sweep, weekly Leiden |
| `pke export out.tar.gz` / `pke wipe` | Backup, delete |

## LLM (optional)

不接 LLM 也能用。extraction 会走 deterministic fallback，judge 会退到 self-report。技能颗粒度会粗一些，但闭环不会断。

想要完整 pipeline，三选一：

```bash
export ANTHROPIC_API_KEY=...                       # claude-haiku-4-5, prompt cache enabled
export PKE_LLM_BASE_URL=http://localhost:8000/v1   # vLLM / sglang / 任何 OpenAI 兼容端点
export OPENAI_API_KEY=...                          # gpt-5-mini
```

我自己平时跑一台 vLLM 起的 Qwen3.5，每次 LLM 调用进 `llm_call_log` 表，记 provider、model、prompt token、completion token、延迟、按 `PRICING` 表算出的美元。`SELECT SUM(cost_usd) FROM llm_call_log WHERE called_at >= datetime('now', '-30 days')` 就能看月成本。Anthropic 默认 Haiku 4.5，因为它便宜、有 prompt cache、判题的能力够。

## Where the data lives

| | |
|---|---|
| `~/.local/share/pke/evidence.db` | SQLite: evidence, skills, mastery, review answers, llm_call_log |
| `~/.local/share/pke/graph/` | Kuzu: skill nodes 加上每周 Leiden 跑出来的 `parent_of` 边，带 bitemporal 时间戳 |
| `~/PKE/inbox/` | 把 ChatGPT 或 Claude.ai 的导出 `.json` 扔进去就自动 import，处理过的会移到 `processed/` |
| `~/.config/pke/secret.toml` | API key 放这里，`pke init` 创建时按 `chmod 600` |

`pke export` 一键打包整个目录，`pke wipe` 一键归零（默认保留 config，加 `--purge-config` 全删）。

## Stack

SQLite for the relational store, Kuzu for the bitemporal skill graph. Identity uses a Matryoshka nomic-embed encoder feeding an hnswlib HNSW index (M=32, ef_construction=200) for fast lookup, plus a `river.cluster.DenStream` online clusterer for streaming dedup; gray-band conflicts get adjudicated by an LLM. Mastery rides on a Half-Life Regression model (`scipy.optimize.minimize` L-BFGS-B) layered over FSRS-4.5 for per-item scheduling. The weekly hierarchy job runs `igraph` Leiden over the centroid graph and writes `parent_of` edges that the nightly decay job consumes for Anderson-style spreading activation. APScheduler drives the cron tier; FastAPI + Textual on the read side; Anthropic Messages or OpenAI Chat Completions on the write side, with prompt caching wherever the provider supports it.

## Status

23 merged PRs, 143 passing tests, 6 SQLite migrations. Two adapters are real daemon-driven producers today (`ClaudeCodeTailerAdapter`, `FileWatcherAdapter`); eight more are scaffolded as `_AdapterBase` subclasses but only get wired when they move into `ACTIVE_PRODUCERS`. The browser extension lives in `pke-ext/` and works on its own; the HTTP proxy adapters need streaming pass-through before they're ready.

This is a personal project. Feel free to file issues for real bugs. I'm not promising long-term maintenance, but if it breaks I probably want to know.

## License

MIT.
