# Sediment

*Close the loop on what AI did for you. / 把 AI 替你做过的事重新捡回来。*

---

## What it is

Sediment watches your AI tool usage on your own machine, distills it into a
graph of skills, and brings the slipping ones back as short review sessions so
the work you did *with* AI gradually becomes something you can also do
*without* AI.

It runs entirely on your laptop. No SaaS, no telemetry, no account.

**Sediment 是什么。** 它在你的本机观察你和 AI 工具的对话，从里面抽出"技能"
（一个命令、一种思路、一段 API 用法……），把它们组织成一张图。当你对某个技能
开始遗忘、或者越来越依赖 AI 做同一件事时，Sediment 在合适的时间给你一道短小的
练习，让你不靠 AI 自己再做一遍。这样，"用 AI 完成"和"我自己会做"之间的距离会
慢慢被填上。

整个系统在你笔记本上跑。没有 SaaS、没有遥测、不用注册。

---

## Why it exists

Anyone who codes with an AI assistant for a few months notices the same thing:
you keep solving the same kind of problem with help, and you stop being able
to solve it without help. Sediment is the smallest tool I could imagine that
turns that drift into something you can see and act on.

It is *not* a parent dashboard, an "AI dependency score", or a guard that
refuses to let AI answer. It is a quiet review loop you can ignore until you
want it.

**为什么写它。** 用 AI 写代码几个月之后，一个常见的体验是：同一类问题被 AI
解决了无数次，你自己却越来越不会做。Sediment 是我能想到的最小工具，能让这种
"会用 AI 做、自己不会做"的漂移变成可以看见、可以处理的东西。

它**不是**家长监控台、不是"AI 依赖指数"、也不会拦住 AI 不让它回答。它只是一个
你想用就用、不想用就忽略的复习闭环。

---

## How it works (one paragraph)

Adapters (Claude Code hook, Claude Code transcript tailer, a local HTTP proxy
in front of OpenAI / Anthropic, a drop-in JSON inbox for ChatGPT/Claude.ai
exports, manual entry) push every observation into an append-only SQLite
table. An extraction LLM call distills each event into named *skills*; an
identity layer merges duplicates against an HNSW index and a DenStream online
clusterer; a mastery layer tracks two numbers per skill (functional vs
unaided) using Half-Life Regression and FSRS-4.5; a selector picks the next
five things to review; a generator writes the review prompt; you answer; a
judge grades. There is a small web app, a TUI, and a daemon that runs the
nightly maintenance jobs.

**一句话讲原理。** Adapter（Claude Code 钩子、对话文件实时跟随、OpenAI /
Anthropic 的本地透明代理、ChatGPT/Claude.ai 导出包的 inbox、手动输入）把观察
到的对话写进一个 append-only 的 SQLite 表。一个 extraction LLM 把每条 evidence
蒸馏成具名的"技能"；identity 层用 HNSW 向量索引 + DenStream 在线聚类做去重；
mastery 层为每个技能维护两个独立数字（"有 AI 帮我能做"、"我自己能做"），用
Half-Life Regression + FSRS-4.5 调度；selector 选出今天最该复习的 5 项；
generator 把它写成一道题；你来答；judge 来打分。配套一个小 web app、一个 TUI、
和一个每晚跑维护任务的 daemon。

---

## Quick start

You need Python 3.11 or 3.12 and [`uv`](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/pengw0048/sediment.git
cd sediment
uv sync
uv run pke init       # writes ~/.config/pke/ and ~/.local/share/pke/
uv run pke serve      # web app on http://127.0.0.1:7421
```

In another terminal:

```bash
uv run pke daemon     # nightly maintenance + real-time tailers
uv run pke tui        # terminal review UI
uv run pke review     # quick 5-item CLI review
```

**快速上手。** 需要 Python 3.11 或 3.12 + [`uv`](https://github.com/astral-sh/uv)。
按上面 6 行命令跑：克隆 → `uv sync` 装依赖 → `pke init` 写本地配置 → `pke serve`
启 web app。另开终端可以 `pke daemon`（后台维护 + 实时摄取）、`pke tui`（终端
复习界面）、`pke review`（命令行做 5 题）。

> Why not `pipx install pke`? Sediment isn't published to PyPI yet — this is a
> local-only project for now. `uv sync` from a clone is the supported install
> path.
>
> 为什么不是 `pipx install pke`？还没发到 PyPI，目前只支持本地 clone +
> `uv sync` 这条路径。

---

## Optional: LLM extraction

By default Sediment runs offline and degrades gracefully without an LLM.
To enable LLM-driven skill extraction, judge, and Socratic intervention, set
one of:

```bash
export ANTHROPIC_API_KEY=...                  # uses claude-haiku-4-5
# or
export PKE_LLM_BASE_URL=http://localhost:8000/v1   # any OpenAI-compatible server
export PKE_LLM_MODEL=qwen35-397b
# or
export OPENAI_API_KEY=...
```

Every LLM call is recorded in `llm_call_log` with token counts, latency, and
derived USD cost. You can audit your spend at any time.

**可选：接 LLM。** 不接 LLM 默认就能跑（只是 extraction / judge 走降级路径）。
想要真的 LLM 抽取技能、判题、Socratic 提示，三选一设环境变量即可：
`ANTHROPIC_API_KEY`（默认 Haiku 4.5）/ `PKE_LLM_BASE_URL`（任何 OpenAI 兼容
端点，比如本地 Qwen）/ `OPENAI_API_KEY`。每次调用都进 `llm_call_log` 表，
token 数 / 延迟 / 折合美元成本随时可查。

---

## What gets stored, and where

| Path | What it is |
|---|---|
| `~/.local/share/pke/evidence.db` | SQLite store: evidence, skills, mastery, review answers, LLM call log |
| `~/.local/share/pke/graph/` | Kuzu graph: bitemporal skill edges |
| `~/.local/share/pke/models/` | Optional local LLM weights |
| `~/.local/share/pke/tailer_offsets.json` | Per-file resume offsets for the watchdog tailer |
| `~/PKE/inbox/` | Drop ChatGPT / Claude.ai export `.json` here; the daemon imports them |
| `~/.config/pke/config.toml` | Settings (adapter enables, thresholds) |
| `~/.config/pke/secret.toml` | API keys (gitignored even if you put this folder in git) |

`pke export <path.tar.gz>` makes a full backup. `pke wipe` deletes everything.

**数据放哪。** 见上表。一行话总结：evidence + 技能图 + 复习记录在
`~/.local/share/pke/`，配置在 `~/.config/pke/`，inbox 在 `~/PKE/inbox/`。
`pke export` 整盘备份，`pke wipe` 整盘删。

---

## What it is not

- Not a SaaS. There is no hosted edition planned.
- Not a "block the AI" tool. AI answers are never gated or rewritten.
- Not a parent dashboard or a productivity score.
- Not finished — see the issues for what's next.

**不是什么。** 不是 SaaS，没有托管版本计划。不会拦 AI、不会改 AI 的输出。
不是家长监控、不是"生产力分数"。也还没写完，剩下的 TODO 在 issues 里。

---

## License

MIT. See [LICENSE](LICENSE).

**许可证。** MIT。
