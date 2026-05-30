# 沉淀 / Sediment

[English README](README.md)

## 为什么有这个项目

AI 能帮我们做掉重复的事，是好事。手算一元二次方程的根、写 commit message、解 merge conflict，这些已经掌握了的事情交给 AI，省下来的时间和注意力可以放在更难的问题、或者真正陌生的东西上。

但反过来，有些东西不该这样交出去。学生做作业的时候碰到一个真正没学过的概念，让 ChatGPT 写完抄上去，那个概念就永远没真的进过脑子。码农写代码、debug、做 ops 的时候同样：一个久没碰的工具、一个没真正理解过的机制，每次都让 AI 替你想，手感慢慢就没了。

沉淀想做的事情很简单：在 AI 回答之前、或者事后回看一段 session 的时候，识别一下里面哪些是你已经熟的、可以放过去的；哪些是新的、或者你很久没练的、值得自己再过一遍的。前者照常用，后者拎出来，让你在合适的时间自己动手做一次。

难的部分不在接入。Claude Code 有 hook、有 transcript 文件，ChatGPT 和 Claude.ai 能导出，浏览器扩展和 HTTP 代理也都是已知方案。难的是怎么把"知识点"这种本来就模糊的东西存好、分好、找得回来。

```bash
git clone https://github.com/pengw0048/sediment.git
cd sediment
uv sync                          # Python 3.11 or 3.12, plus uv
uv run pke init
uv run pke serve                 # http://127.0.0.1:7421
```

## 两种模式

**事后检查。** `pke daemon` 在后台监听 Claude Code 的 transcript 文件和 `~/PKE/inbox/` 里的导出包，把每段对话蒸馏成具名的技能。每天有空打开 `pke tui` 或 web 端做 5 题，题目根据你哪个技能的"不靠 AI 自己能做"指标在滑、由 LLM 当场写出来 —— 可能让你解释一遍，可能换个变种让你做，可能让你不查文档先猜第一步。

**实时介入（可选）。** 在 AI 回答之前，如果调度器判断你正在问的是一个你已经被它接管太久的技能，会先弹一个 Socratic 短问题让你先答一下。你可以一键 dismiss 继续问 AI，连续 dismiss 5 次该 source 自动降一档 24 小时，不会反复打扰。

```
pke serve          web UI
pke tui            terminal review
pke review         headless 5-item batch
pke daemon         tailers + nightly maintenance
pke export FILE    full backup
pke wipe           delete everything (config kept unless --purge-config)
```

## LLM (optional)

不接 LLM 也能用：extraction 走 deterministic fallback，judge 退化成 self-report，技能颗粒度会粗一些，但闭环不断。完整 pipeline 需要一个 LLM endpoint，三选一：

```bash
export ANTHROPIC_API_KEY=...                       # claude-haiku-4-5
export PKE_LLM_BASE_URL=http://localhost:8000/v1   # OpenAI-compatible endpoint
export OPENAI_API_KEY=...                          # gpt-5-mini
```

每次 LLM 调用进 `llm_call_log` 表，provider、model、token、延迟、按 PRICING 折算的美元都有，月成本随时 `SELECT SUM(cost_usd) FROM llm_call_log WHERE called_at >= datetime('now', '-30 days')`。Anthropic 默认 Haiku 4.5：便宜、有 prompt cache、判题够用。

## Where the data lives

| | |
|---|---|
| `~/.local/share/pke/evidence.db` | SQLite: evidence, skills, mastery, review answers, llm_call_log |
| `~/.local/share/pke/graph/` | Kuzu: skill nodes 加每周 Leiden 跑出来的 `parent_of` 边，bitemporal |
| `~/PKE/inbox/` | ChatGPT / Claude.ai 的导出 `.json` 扔这里就自动 import |
| `~/.config/pke/secret.toml` | API key，`pke init` 创建时 `chmod 600` |

## 现在什么能跑

跑得通：Claude Code 的 transcript 实时 tailer、`~/PKE/inbox/` 的导出包 import，三个入口（web / TUI / CLI）的复习闭环，nightly decay + 每周 Leiden + EDC merge sweep，接 Anthropic / OpenAI / 本地 vLLM 三种 LLM。

还没跑通：HTTP 代理 adapter（OpenAI / Anthropic 的实时拦截）能起 server 但 SSE pass-through 没做，所以 streaming 抓不全；浏览器扩展能独立装但还没接进 daemon 的事件流。

刚写完、自己用着、不算 battle-tested。

## License

MIT.
