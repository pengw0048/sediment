# 沉淀 / Sediment

高中生让 ChatGPT 把一道数学题讲一遍，跟着步骤抄下来，能拿满分。下次考试遇到同类型还是不会。她说她"明明做过"。

程序员用 Claude Code 改 Kubernetes 配置、写 Cypher 查询，让 AI 一遍写完跑通。过两周自己重新动手还是不会。他也"明明做过"。

两边是同一件事：AI 帮你完成了任务，但学到这一步没发生。因为你盯着的是要做的活，不是要学的东西。

传统补救办法是事后整理：记笔记、做闪卡、Anki 复习。这些都要求你先意识到自己刚学到了什么、再花时间把它结构化。而恰恰是用 AI 的时候，这种意识最弱 —— 你正在赶进度。

沉淀想把这一步自动化。

它在本机跑一个小后台，看着你跟 AI 之间的对话流水（Claude Code 的 transcript 文件、ChatGPT 和 Claude.ai 的导出包），从里面挑出技能。颗粒度比"FastAPI"或者"二次函数"细：怎么用 `kubectl describe` 看 pod 的退出码、求根公式什么时候应该改用配方法、Cypher 怎么写 variable-length path。每个技能维护两个独立的数，一个是"用 AI 帮你的时候你能做到什么程度"，另一个是"不靠 AI 自己能做到什么程度"。前者通常很高，后者通常滑得比你想象的快。当后者开始往下走，调度器把它排进今天的 5 道复习题里推到你面前，由 LLM 当场出题，由 LLM 当场判分，再回填两个数。

没有云端，没有遥测，没有账号。

```bash
git clone https://github.com/pengw0048/sediment.git
cd sediment
uv sync
uv run pke init
uv run pke serve     # http://127.0.0.1:7421
```

需要 Python 3.11 / 3.12 + [uv](https://github.com/astral-sh/uv)，没了。

## The actual loop

`pke daemon` runs in the background and listens to your Claude Code transcripts plus the `~/PKE/inbox/` drop folder. Whenever you've got five minutes, open `pke tui` or hit the web app at `http://127.0.0.1:7421` and answer the five items it's queued. Items come in four shapes depending on what the scheduler thinks will catch the gap — an explain-back ("describe what `Depends()` does in your own words"), a variant ("now make this dependency re-run on every request, what changes"), a Socratic-first-step ("you've just seen the pod is in CrashLoopBackOff, what's the first kubectl invocation you'd reach for"), or a straight replay-self-try. You answer, the grader (regex / code-exec / self-report / LLM judge, picked per-item) gives back a grade, and FSRS-4.5 plus a Half-Life Regression model decide when that skill comes back.

```
pke serve          web UI
pke tui            terminal review
pke review         headless 5-item batch (good for cron)
pke daemon         tailers + nightly maintenance
pke export FILE    full backup
pke wipe           delete everything (config kept unless --purge-config)
```

## LLM (optional)

不接 LLM 也能用。extraction 走 deterministic fallback，judge 退化成 self-report，技能颗粒度会粗一些，但闭环不断。

完整 pipeline 需要一个 LLM endpoint，三选一：

```bash
export ANTHROPIC_API_KEY=...                       # claude-haiku-4-5
export PKE_LLM_BASE_URL=http://localhost:8000/v1   # 任何 OpenAI 兼容端点，比如本地 vLLM 起一个 Qwen
export OPENAI_API_KEY=...                          # gpt-5-mini
```

Anthropic 默认 Haiku 4.5 因为它便宜、支持 prompt cache、判题能力够。每次 LLM 调用都进 `llm_call_log` 表 —— provider、model、prompt token 数、completion token 数、延迟、按 `PRICING` 表折算的美元。月成本随时 `SELECT SUM(cost_usd) FROM llm_call_log WHERE called_at >= datetime('now', '-30 days')`。

## Where the data lives

| | |
|---|---|
| `~/.local/share/pke/evidence.db` | SQLite。evidence、skill 节点、mastery、review 记录、llm_call_log 都在里面 |
| `~/.local/share/pke/graph/` | Kuzu 图。每周 Leiden 跑出来的 `parent_of` 边带 bitemporal 时间戳 |
| `~/PKE/inbox/` | ChatGPT / Claude.ai 的导出 `.json` 扔进来就自动 import，处理过的移到 `processed/` |
| `~/.config/pke/secret.toml` | API key。`pke init` 创建时按 `chmod 600` |

## Stack

SQLite for the relational store, Kuzu for the bitemporal skill graph. Identity dedup uses a nomic Matryoshka encoder feeding an hnswlib HNSW index (M=32, ef_construction=200) plus a `river.cluster.DenStream` online clusterer for streaming pairs; gray-band conflicts get adjudicated by an LLM with a three-region cosine threshold. Mastery rides on a Half-Life Regression model that `scipy.optimize.minimize` (L-BFGS-B) refits offline, layered over FSRS-4.5 for per-item scheduling. The weekly hierarchy job runs `igraph` Leiden over the skill centroid graph and writes `parent_of` edges that the nightly decay job consumes for Anderson-style spreading activation. APScheduler drives the cron tier (decay, EDC merge sweep, drift metrics, Leiden, vacuum). FastAPI plus Textual on the read side. Anthropic Messages or OpenAI Chat Completions on the write side, with prompt caching wherever the provider supports it.

## What works, what doesn't

跑得通的：Claude Code 的 transcript 实时 tailer 加 `~/PKE/inbox/` 的 ChatGPT / Claude.ai 导出 import；evidence 进库、extraction 产出 skill 节点、identity 去重；review 闭环（出题、答题、判分、回填 mastery）三个入口 web / TUI / CLI 都通；nightly decay 加每周 Leiden 加 EDC merge sweep 全部由 daemon 调度；接 Anthropic / OpenAI / 本地 vLLM 三种 LLM 都跑过，cost 真的进表能 SELECT。

还没跑通的：HTTP 代理 adapter（OpenAI / Anthropic proxy）能起 server 但没做 SSE 流式 pass-through，所以对 streaming 的 chat 抓不全；浏览器扩展独立能装但还没接进 daemon；没发 PyPI、没测过 Windows、没测过远程 ssh 进来的多用户场景。

刚写完、自己用着、不算 battle-tested。schema 还有可能小改（迁移会写，但回滚没保证）。

个人项目。bug 可以提 issue，但我不承诺长期维护 —— 东西坏了我大概率想知道，但修不修看心情。

## License

MIT.
