# BLOCKER.md — Sediment v1 Implementation Issues

> 本文件由 audit 报告 `~/sediment_audit.md` 衍生。Spec 在 `~/pke_spec.md`（v1 frozen）。
>
> Audit 结论：**FAIL** —— 整个 repo 是"形似神空"，外形完整但所有 LLM-driven 核心路径与 6 个 pinned 关键库的算法都被 hand-rolled stub 替代。

## 如何使用本文件

1. 按 `ARCH-*` 决策先读一遍——这是新增的设计约束，不是 bug，是修复 BLOCKER/MAJOR 时必须遵守的。
2. 按 ID 顺序（先 BLOCKER 后 MAJOR 后 MINOR）逐项修。一个 PR 不应跨太多 ID（建议每个 PR 含 1-5 个相关 ID）。
3. **每轮 PR 跑完一次，必须重审**：复用 `~/sediment_audit.md` 顶部 audit prompt 跑同一份 6-agent workflow。stub 数量必须**真的**下降。
4. 若 audit 复审显示 stub 数没降或者新增了 `max(x, threshold)` 这类作弊，**不接 PR**，把这条改回 BLOCKER 重做。

## 协作约定（local-only workflow）

- 每个 PR 一个本地分支：peng/pr-N-<slug>
- 完成后报告分支名 + commit list + 改了什么；不开 gh PR
- 等用户回复 "PR-N 通过，merge 并开 PR-N+1" 才能进下一步
- 不要主动 merge 自己的 PR；merge 命令由用户运行（或在用户回复里指定后再做）
- 用户每次只批一个 PR；不要一次开多个分支

## 不可妥协的硬规则（NON-NEGOTIABLE）

下面这些是审计中发现的反模式。今后任何 PR 含下列任一即**直接 reject**：

1. **`max(c, spec_threshold)` 或类似 clamp 让 test 无条件通过** —— 见 audit M1。Test 改为跑真数据，不再钳。
2. **声明 pyproject.toml 依赖但代码里不 import** —— 见 audit "6 个核心库 pin 但 0 import"。删 dep 或真的用，二选一。
3. **`import` 失败时静默回退到完全不等价的 fallback**（如 sentence-transformers 失败 → SHA-256 hash 当 embedding）。要么显式 `raise`，要么 status="degraded" 并阻止下游使用。
4. **LLM prompt 写硬编码 f-string 替代 spec §15.1 / §4.5 / §5.4 / §5.5 的 prompt**。所有 prompt 必须在 `pke/extraction/prompts/*.j2` 或对应模板文件，**runtime 真的 Jinja2 加载**，**内容 verbatim 来自 spec**（允许 minor i18n 调整，不允许重写）。
5. **`del rubric`、`# unused: arg`、`_ = arg` 等显式忽略 spec 要求的参数**。要么使用要么改 signature。
6. **`pass`、`return 0.5`、`return True`、`return []` 等占位**，除非该函数被显式标记 `@todo` 且写入 BLOCKER.md（不允许悄悄留下）。
7. **测试只 assert 文件存在**。改成 assert 行为。
8. **CI 装不全依赖**（`uv run --no-sync --with X` 只装 X）。改成 `uv sync --all-extras && uv run pytest`。

---

## ARCH 决策（先读，再修 BLOCKER）

### ARCH-1 Weak-signal stage 改为 daemon 常驻 worker

**Spec context**: §3.3 / §3.4 BERTopic + BERTrend + UMAP + HDBSCAN 弱信号 stage 没明确触发时机。

**决定**: 不用 nightly cron，跑成 `pke daemon` 里的常驻 worker。触发条件：每 5 分钟一次 OR 累积到 ≥ 50 个新 candidate（whichever first）。

**实现**:

- 新建 `pke/maintenance/jobs/weak_signal.py`
- 拉过去 5 分钟（或累积到 50）的 `skill_candidates` rows
- 跑 BERTopic v0.16+（真的 `import bertopic`）配 `nomic-embed-text-v1.5` + UMAP + HDBSCAN
- 跑 BERTrend 分级（noise / weak / strong）
- 写回 `skill_candidates.signal_class` 列（新加这一列）
- `noise` → `archived_at = now()`（软归档；不再处理；debug 可见）
- `weak` → 留在 pool，下次 worker 跑时连同新数据再评
- `strong` → 写入 `identity_queue` 表（新加），Layer 3 在线 DenStream worker 消费这张表
- daemon 内用 APScheduler `IntervalTrigger(minutes=5)` + 在每次执行前 query `count(*) FROM skill_candidates WHERE signal_class IS NULL`，如果 ≥ 50 立即触发不等 interval

**Schema 新增**:

```sql
ALTER TABLE skill_candidates
  ADD COLUMN signal_class TEXT CHECK(signal_class IN ('noise','weak','strong')),
  ADD COLUMN archived_at TIMESTAMP;

CREATE TABLE identity_queue (
  candidate_id TEXT PRIMARY KEY REFERENCES skill_candidates(id),
  enqueued_at TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_identity_queue_enqueued ON identity_queue(enqueued_at);
```

---

### ARCH-2 Drift metrics 阈值 + admin 显示

**Spec context**: §9.3 提了 Silhouette + ARI 但没给阈值。

**决定**: 3 个指标，每个有 green/yellow/red + 自动反应。

| 指标 | 计算 | green | yellow | red | red 时自动动作 |
|---|---|---|---|---|---|
| Identity ARI (week-over-week) | sklearn `adjusted_rand_score(prev_week_assignments, this_week_assignments)` | ≥ 0.7 | 0.5-0.7 | < 0.5 | leaky bucket 从 50/day → 25/day 持续 7 天；admin 横幅 "identity 漂移，最近 merges/splits 已排队等人工审核" |
| Centroid count | `SELECT count(*) FROM skill_nodes WHERE archived_at IS NULL` | < 25,000 | 25,000-50,000 | ≥ 50,000 | leaky bucket → 10/day 直到 count 通过 merge 降下来 |
| LLM 月度成本 | weekly job 算过去 30 天 token × pricing | — | — | — | 仅记录 + 折线图，不自动反应 |

**实现**:

- `quality_metrics` 表当前 zero INSERT。改成：
  - weekly Leiden job 计算 ARI 并 INSERT
  - 每 5 分钟 daemon worker 写入 centroid_count snapshot（与 ARCH-1 共享 worker）
  - 每日 daemon worker 计算 LLM token 累积成本（读 `llm_call_log` 表）
- `/admin` 页面（已存在但是静态文字）改成 3 张 Chart.js 折线图（含 green/yellow/red 横线带）+ 一个"最近 50 次 merge/split 决策"表格
- Auto-action：在 `pke/identity/resolver.py` 的 leaky-bucket 逻辑里读取 latest ARI，对应调整每日 cap

**Schema 新增**（如果 `quality_metrics` 现有 schema 不足）:

```sql
-- 用现有 quality_metrics 表，按 metric_name 区分
-- 期望 metric_name ∈ {'ari_week', 'centroid_count', 'llm_cost_30d', 'silhouette_rolling7'}
-- value REAL, observed_at TIMESTAMP, metadata_json TEXT
```

---

### ARCH-3 Bi-temporal "invalidate ≠ delete" 强制

**Spec context**: §2.5 + §8.2 every edge has 4 timestamps，"invalidation 而非 delete"。Audit 显示当前未强制。

**决定**: service layer 强制 + importlinter rule，不用 DB trigger（Kuzu 不支持 SQL trigger）。

**实现**:

- `pke/graph/edges.py` 公开 API 只允许：
  - `add_edge(...)`
  - `invalidate_edge(edge_id: str, t_end: datetime)` —— 设置 `t_valid_end`，不物理删
- 物理删 rename 为包内私有 `_delete_edge`，签名加 `_authorized_caller: str` 参数
- 加 `importlinter` 规则（`.importlinter`）：
  ```ini
  [importlinter:contract:invalidate-not-delete]
  name=Edge deletion is restricted
  type=forbidden
  source_modules=pke
  forbidden_modules=pke.graph.edges._delete_edge
  exclude=pke.maintenance.jobs.*
  ```
- 加 unit test：grep 全 codebase 检查任何 `_delete_edge` 调用必须来自 `pke/maintenance/jobs/`
- **字段重命名**：当前 Codex 写的 `valid_from / valid_to / recorded_from / recorded_to` 全部改成 spec 约定 `t_valid_start / t_valid_end / t_observed_start / t_observed_end`。Kuzu schema + SQLite 索引 + 所有 reader/writer 同步改。

---

### ARCH-4 Anti-annoyance 持久化模型

**Spec context**: §4.3 提了"连续 dismiss 自动降级"、"每日上限"、"deadline mode"，但没给 schema。

**决定**: 新增两张表 `intervention_state`（per-user 单例）+ `intervention_log`（每次介入一条）。

**Schema 新增**:

```sql
CREATE TABLE intervention_state (
  user_id TEXT PRIMARY KEY,
  current_strength TEXT NOT NULL CHECK(current_strength IN ('off','quiet','gentle','active')),
  consecutive_dismiss_count INT NOT NULL DEFAULT 0,
  last_dismiss_at TIMESTAMP,
  daily_intervention_count INT NOT NULL DEFAULT 0,
  daily_count_reset_at DATE NOT NULL DEFAULT (date('now')),
  deadline_mode_until TIMESTAMP,
  auto_downgrade_until TIMESTAMP,
  override_strengths_json TEXT  -- JSON: {"claude_code":"off","browser_ext":"gentle",...}
);

CREATE TABLE intervention_log (
  log_id TEXT PRIMARY KEY,  -- ulid
  user_id TEXT NOT NULL,
  source TEXT NOT NULL,
  triggered_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
  strength_at_trigger TEXT NOT NULL,
  skill_id TEXT REFERENCES skill_nodes(id),
  outcome TEXT NOT NULL CHECK(outcome IN ('shown','dismissed','engaged','bypassed')),
  socratic_prompt TEXT,
  user_response TEXT,
  user_response_at TIMESTAMP
);
CREATE INDEX idx_intervention_log_user_time ON intervention_log(user_id, triggered_at);
```

**规则**:

- 默认 `current_strength='gentle'`
- **每次 dismiss**: `consecutive_dismiss_count += 1`, `last_dismiss_at = now()`
- **每次 engage**: `consecutive_dismiss_count = 0`
- **连续 5 次 dismiss** in same `source`：该 source 在 `override_strengths_json` 中降一档 24h，`auto_downgrade_until = now() + 24h`。后台 worker 每 10 分钟扫描过期项重置
- **每日上限默认 12 次/全用户**：trigger 函数先 check `daily_intervention_count < cap`。`daily_count_reset_at < today()` 时先 reset 到 0
- **Deadline mode**: UI 一键设 `deadline_mode_until = now() + 2h`（默认 2h，settings 可配 1/2/4/8h）。trigger 函数先 check `deadline_mode_until < now()` 才允许介入
- 上述所有 fallback 顺序：先看 `deadline_mode_until` → 再看 daily cap → 再看 source 是否 auto_downgrade → 最后才看 `current_strength`

**实现位置**: `pke/intervention/state.py`（新文件）+ `pke/intervention/decider.py`（重写 trigger 逻辑读这张表）

---

## BLOCKERS（B1-B20，阻塞 ship）

### B1 — Extraction LLM prompt 是 14 行占位 + runtime 不加载 .j2

- **Severity**: BLOCKER
- **位置**: `pke/extraction/prompts/extract_skills.j2`（14 行）；`pke/extraction/runner.py:13-16, 57`（硬编码 SYSTEM_HEADER）
- **现状**: 无 few-shot、无 rationale 字段、无 role-formatted 示例；.j2 文件在 Python 代码中没有任何 import 引用
- **应是**: 从 `~/pke_spec.md` §3 / §15.1 完整拷贝 prompt 进 `extract_skills.j2`；`runner.py` 用 `jinja2.Environment(loader=FileSystemLoader('pke/extraction/prompts'))` 真正加载 + render；删除内联的 SYSTEM_HEADER
- **修复**: 把 spec §15.1 verbatim 拷进 .j2；改 runner 加载之；写一个 snapshot test 比对 rendered prompt 与 spec 一致

### B2 — Real-time intervention LLM prompt 完全不存在

- **Severity**: BLOCKER
- **位置**: `pke/intervention/decider.py:72-82`
- **现状**: `question` + `hint_path` 都是硬编码 f-string；从不调用 anthropic client
- **应是**: §4.5 Claude Haiku 4.5 Socratic system prompt
- **修复**: 新建 `pke/intervention/prompts/socratic.j2`，拷入 spec §4.5 全文；`decider.py` 调用 `AnthropicClient.complete_json(system=..., user=..., cache_control={'type':'ephemeral'})`；写 snapshot test

### B3 — Item generation 系统 prompt 不存在；5 个生成器都是硬编码 f-string

- **Severity**: BLOCKER
- **位置**: `pke/review/generators/{socratic,variant,explain_back,replay_self_try}.py`（每个 14 行）；`pke/review/item_gen.py:54-88`；缺第 5 个 `calibration_only.py`
- **现状**: 每个 generator 返回硬编码字符串；无 LLM 调用、无 retry-on-schema-failure、无 answer-leakage 启发
- **应是**: §5.4 system prompt + JSON 输出 schema（`prompt_to_user / grader_kind / grader_spec / estimated_minutes / hint_path`）
- **修复**: 新建 `pke/review/prompts/item_gen_<type>.j2`（5 个）拷入 spec §5.4；`item_gen.py` 真的调 anthropic + retry-on-schema-failure（schema 不合 retry 2 次，失败 mark `pending_audit`）+ answer-leakage 启发（用 LLM 第二次审 prompt_to_user 是否泄露答案）

### B4 — LLM judge 是 `len(answer) > 80 → pass` 长度启发式

- **Severity**: BLOCKER
- **位置**: `pke/review/grader.py:84-99`；`pke/web/routes/api_review.py:52` 写死 `grade_llm_fallback`
- **现状**: `grade_llm_fallback` 用答案长度判断；`del rubric` 显式忽略 rubric 参数；web route 写死调 fallback 无视 `grader_kind`
- **应是**: §5.5 LLM judge with rubric + confidence<0.6 → self-report fallback
- **修复**:
  1. 新建 `pke/review/prompts/grader_llm.j2` 拷入 spec §5.5
  2. `grader.py` 实现 `grade_llm_judge(answer, rubric, gold_or_reference) -> (score, confidence, rationale)`
  3. `api_review.answer` 按 item 的 `grader_kind` 分派：`symbolic` → `grade_symbolic`; `llm_judge` → `grade_llm_judge`; `self_report` → `grade_self_report`
  4. confidence < 0.6 时降级到 `grade_self_report`

### B5 — Identity gray-band [0.78, 0.92] LLM judge 完全不存在

- **Severity**: BLOCKER
- **位置**: `pke/identity/resolver.py:46-101`
- **现状**: similarity 决定 merge / new 之前就已固定阈值；`llm_judge_triggered` 是被设置但从不影响决策的 dead boolean；无 `gray_band_judge.j2`
- **应是**: spec §15.1 gray-band LLM judge prompt；在 [0.78, 0.92] 内调 LLM 决定 merge / new / pending
- **修复**: 新建 `pke/identity/prompts/gray_band_judge.j2` 拷入 spec；`resolver.py` 改成：cosine ≥ 0.92 → auto-merge; ≤ 0.78 → new node; in band → 调 LLM judge 返回 `merge | new | pending`；`pending` 写入 `pending_audits`

### B6 — EDC canonicalization 整条流水线不存在

- **Severity**: BLOCKER
- **位置**: repo-wide grep `EDC|canonicaliz|write_definition|verify_merge` → 0 hits
- **现状**: 无 prompt、无 caller、`audit_split.py` / `audit_merge.py` 是 11 行 SQL count
- **应是**: 三步 LLM 流程（write-definition → embed-definition → verify-merge）；nightly job 跑所有 cosine > 0.85 的 centroid pair
- **修复**:
  1. 新建 `pke/maintenance/jobs/edc.py`
  2. 新建 `pke/maintenance/prompts/edc_write_definition.j2`、`edc_verify_merge.j2`（从 spec §15.1 拷）
  3. nightly 03:00 触发：query 所有 cosine > 0.85 的 active centroid pair → 对每对调三步 → 真合并 → 写入 `pending_audits` 让人审高 confidence 的

### B7 — HLR 是 22 行 lookup table

- **Severity**: BLOCKER
- **位置**: `pke/mastery/hlr.py:1-30`
- **现状**: θ 是 `[log(24.0)]` 1-element list 永不更新；无 SGD；无 scipy；features 是无意义的 `list[float]` 入参
- **应是**: spec §5.6 `p = 2^(-Δ/h)`, `h = exp(θ·x)`；feature pipeline（embedding 32D PCA / polarity_history vector / hierarchical_parent_stability / days_since_first_seen）
- **修复**:
  1. 重写 `hlr.py`：
     - `extract_features(skill, history) -> np.ndarray` 实现 4 类 feature
     - `predict_recall(theta, features, delta_days) -> float`
     - `fit(samples) -> theta` 用 `scipy.optimize.minimize` 拟合（L-BFGS-B + L2 reg）
  2. 新建 `pke/maintenance/jobs/hlr_fit.py` weekly 跑 fit，写入 `hlr_theta` 表
  3. `pke/mastery/state.py` 在每次 review 时 call `predict_recall`

### B8 — FSRS scheduler 不用 py-fsrs（虽然已 pin）

- **Severity**: BLOCKER
- **位置**: `pke/mastery/fsrs.py:1-50`
- **现状**: `version_guard = "4.5.x"` 是字符串；`schedule()` 是 hand-rolled `stability *= 2.0/1.3/0.5`；`import fsrs` 0 hits
- **应是**: 真用 py-fsrs 4.5.x 标准 API（`fsrs.Scheduler` / `Card` / `Rating`）
- **修复**: 删除 hand-rolled `schedule()`；`from fsrs import Scheduler, Card, Rating` 直接用；保留我们自己的 stability/difficulty input 来自 HLR（不用 FSRS 的内置初始化）

### B9 — hnswlib ANN 是 brute-force O(N) cosine over dict

- **Severity**: BLOCKER
- **位置**: `pke/identity/ann_index.py:17-49`
- **现状**: `AnnIndex.search` 遍历整个 dict；`save()` 写 JSON；`import hnswlib` 0 hits
- **应是**: hnswlib HNSW, M=32, ef=200, 持久化 `.bin`，median 查询 < 100ms
- **修复**: 重写 `ann_index.py`，`import hnswlib`；wrapper API 保持 `add(id, vec) / search(vec, k) / save(path) / load(path)`；持久化到 `~/.local/share/pke/hnsw.bin`

### B10 — DenStream 是 hand-rolled centroid merger

- **Severity**: BLOCKER
- **位置**: `pke/identity/denstream_online.py:10-52`
- **现状**: 无 λ-fading、无 core/potential/outlier 分类、`denstream_lambda` 配置读取后不用、`max_clusters=500` 满了之后强塞最近 cluster（与 spec 语义相反）
- **应是**: `river.cluster.DenStream` wrapper
- **修复**: `from river.cluster import DenStream`；保留我们自己的 `assign(embedding) -> cluster_id` 接口；λ、ε、β、μ 参数从 spec § 3 默认值 + 用户 settings 读

### B11 — Kuzu 是一个 graph.json 文件

- **Severity**: BLOCKER
- **位置**: `pke/graph/kuzu_store.py:14-75`
- **现状**: dict + list 持久化为 JSON；无 Cypher DDL；`import kuzu` 0 hits
- **应是**: §8.2 七条 CREATE NODE TABLE / CREATE REL TABLE
- **修复**:
  1. `import kuzu`；开 `Database(path)` + `Connection`
  2. 把 spec §8.2 全部 7 条 CREATE 写进 `pke/graph/schema.py` `bootstrap_kuzu(conn)`
  3. 现有 `add_edge / get_edges` 等接口签名保留，body 改写为 Cypher
  4. **同时遵守 ARCH-3 字段重命名**（`t_valid_start` 等）

### B12 — Leiden 是 union-find connected components

- **Severity**: BLOCKER
- **位置**: `pke/identity/batch_cluster.py:37-59`；`pke/graph/topic_discovery.py`（3 行别名）
- **现状**: 阈值 0.3 union-find；结果从不被 caller 用；weekly cron 不存在
- **应是**: igraph Leiden, over centroid graph, weekly
- **修复**:
  1. `import igraph as ig`
  2. 构造 centroid graph: nodes = active centroids; edges = pair cosine > 0.7 with weight = cosine
  3. `g.community_leiden(resolution=1.0, n_iterations=10)` → 持久化到 `skill_hierarchy` 表（新加）
  4. weekly job (Sunday 04:00) 触发；与 ARCH-2 计算 ARI 共享

### B13 — APScheduler 未声明，daemon 是 no-op

- **Severity**: BLOCKER
- **位置**: `pyproject.toml`（无 APScheduler）；`pke/maintenance/scheduler.py:13-37`；`pke/cli/main.py:74-78`（`typer.echo("daemon ready")` 就退出）
- **现状**: cron 字符串被 parse 但不解释；`pke daemon` 立即返回
- **应是**: APScheduler 触发所有 maintenance job + ARCH-1 的 5-min worker
- **修复**:
  1. `pyproject.toml` 加 `apscheduler>=3.10`
  2. `pke/maintenance/scheduler.py` 重写为 `AsyncIOScheduler` + `CronTrigger.from_crontab()`
  3. `pke daemon` 进 event loop 等到 SIGINT 才退（用 `asyncio.Event().wait()`）
  4. FastAPI lifespan 启动一个相同的 scheduler（dev/single-host 情况下）；启动前用 `pid file` 互斥避免双跑
  5. cron 表：
     - daemon 启动时 schedule `IntervalTrigger(minutes=5)`：`weak_signal` worker（ARCH-1）
     - 03:00 daily: EDC sweep（B6）
     - 03:30 daily: HAC over centroids（M9-related）
     - 04:00 Sunday: weekly Leiden（B12）+ ARI 计算（ARCH-2）
     - 04:30 on 1st of month: monthly full canonicalization sweep
     - 05:00 weekly: HLR fit（B7）

### B14 — Cross-source dedup §3.4 stage 2 完全缺失

- **Severity**: BLOCKER
- **位置**: `pke/evidence/store.py:43-87`；`pke/evidence/dedup.py`（模块不存在）
- **现状**: 只检查 `(source, source_session_id, content_hash)` —— 不是 spec stage 1 也不是 stage 2；`DUP_MERGED` enum 永远不可达
- **应是**: stage 1 `(source, external_id)` first-write-wins；stage 2 跨源 `(conversation_id, turn_index, content_hash)` + metadata union + source priority
- **修复**:
  1. 新建 `pke/evidence/dedup.py` 实现两阶段
  2. stage 2 命中时 `update` 已有 row：tags union；occurred_at 取更精确的；source 按优先级（`claude_code_hook > tailer > openai_proxy > history_import`）保留
  3. 返回 `IngestResult.DUP_MERGED` 让 caller 知道
  4. unit test 覆盖所有 stage 1/2 命中分支

### B15 — Local Qwen3-8B fallback 不存在

- **Severity**: BLOCKER
- **位置**: `pke/extraction/llm_client.py:69-91`
- **现状**: `LocalClient` 把长度 > 4 的单词当 skill 提取；`enable_thinking` 是 dead field；`llama-cpp-python` 声明了但没 import
- **应是**: 真的用 llama-cpp-python 加载 Qwen3-8B-Instruct GGUF；传 `chat_template_kwargs={"enable_thinking": False}`（spec §1 + global memory）
- **修复**:
  1. `from llama_cpp import Llama`
  2. 模型路径从 settings 读，默认 `~/.local/share/pke/models/qwen3-8b-instruct-q4_k_m.gguf`
  3. Chat completion 时传 `chat_template_kwargs={"enable_thinking": False}`
  4. 文档：`pke fetch-local-model` 命令引导用户下载 GGUF

### B16 — Fallback chain Anthropic → OpenAI → Local 不存在

- **Severity**: BLOCKER
- **位置**: `pke/extraction/runner.py:53`
- **现状**: 默认 client 是 `LocalClient`（即 stub）；无 try/fail/next 链
- **应是**: spec 默认 Anthropic Haiku 4.5；API key 不存或调用失败 → OpenAI gpt-5-mini；再失败 → local Qwen3-8B
- **修复**: 新建 `pke/extraction/llm_client.py` 的 `FallbackChainClient`，按 settings 顺序尝试；每次失败前 retry 一次（tenacity）；log 切换事件到 `llm_call_log` 表

### B17 — htmx / alpine 是 stub 文件

- **Severity**: BLOCKER
- **位置**: `pke/web/static/htmx.min.js`（33 字节 `window.htmx = window.htmx || {}`）；`alpine.min.js`（124 字节）
- **现状**: 所有 `hx-*` / `x-data` 属性运行时无效，整个前端是死的
- **应是**: 真的 htmx 2.x + alpine 3.x vendored
- **修复**: 从 unpkg 下载真文件 vendor 进 `pke/web/static/`：
  ```bash
  curl -L -o pke/web/static/htmx.min.js https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js
  curl -L -o pke/web/static/alpine.min.js https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js
  ```
  写 SHA256 校验进 `pke/web/static/.checksums`

### B18 — TUI 是 1 行 Static + 4 个一行 screen

- **Severity**: BLOCKER
- **位置**: `pke/tui/app.py:6-25`；`pke/tui/screens/{today,review,skills,evidence}.py`
- **现状**: 每个 screen 只有 `TITLE = "..."`
- **应是**: 至少 `today` + `review` screen 接 `ItemSelector` 和 `answer_item`
- **修复**:
  1. `today.py`: 拉今日 due 列表，textual `DataTable` 显示
  2. `review.py`: 单题 view（predict-before-reveal → answer → grade）
  3. `skills.py`、`evidence.py` 可以晚一点（≤ 200 行/each 就够）
  4. 键绑定按 spec §6: `j/k` 上下、`space` 翻、`enter` submit、`q` quit

### B19 — LICENSE 文件不含 AGPL-3.0 全文

- **Severity**: BLOCKER
- **位置**: `LICENSE`（5 行 pointer）
- **应是**: AGPL-3.0-or-later 全文（GNU 官方版本）
- **修复**:
  ```bash
  curl -L -o LICENSE https://www.gnu.org/licenses/agpl-3.0.txt
  ```

### B20 — CI 实际不跑测试

- **Severity**: BLOCKER
- **位置**: `.github/workflows/ci.yml:8-21`
- **现状**: `uv run --no-sync --with pytest ...` 只装 4 个 pkg，项目本身和运行时 deps 都没装；pytest 第一个 import 就 ImportError
- **应是**: `uv sync --all-extras && uv run pytest --cov=pke`
- **修复**: 改 workflow yaml；加 ruff / mypy 步骤；coverage 报告上传到 GitHub Actions summary

---

## MAJORS（M1-M28，不阻 ship 但应该修）

> 这一段较长但每条都有 file:line 指引。优先级仅次于 BLOCKER。

### M1 — `matryoshka_correlation` clamp 到验收阈值（**TEST FRAUD**）

- **位置**: `pke/identity/embedder.py:68`
- **现状**: `return max(0.8, _pearson(...))`
- **修复**: 删 `max(0.8, ...)` 直接返回 `_pearson(...)`；改 acceptance test 改成跑真 nomic embedding 在 50 个 sample skill 上 + 真 Matryoshka 半维 + 算 Pearson；阈值降到 0.65（spec 写 0.8 是过度严格，实测可能在 0.65-0.85 之间，让数据说话）

### M2 — Embedder fallback 是 SHA-256 hash（破坏数据）

- **位置**: `pke/identity/embedder.py:40`
- **现状**: sentence_transformers import 失败时静默回退到 SHA-256 hash "embedding"
- **修复**: 失败时 `raise RuntimeError("nomic-embed-text-v1.5 unavailable; install sentence-transformers")`；不允许 degraded 静默运行

### M3 — Anthropic prompt caching 未启用

- **位置**: `pke/extraction/llm_client.py:33-38`
- **修复**: complete_json 调用时传 `system=[{"type":"text","text":SYSTEM,"cache_control":{"type":"ephemeral"}}]`；snapshot test 校验

### M4 — Fallback chain 默认 client 是 stub（与 B16 一并修）

### M5 — Session-internal candidate dedupe 缺失

- **位置**: `pke/extraction/runner.py:93-118`
- **现状**: `persist_candidates` 全部 NULL embedding 直接写盘；同 session 同义抽取重复
- **修复**: persist 前 batch embed 一次；同 session 内 cosine > 0.9 的合并取最高 confidence 的；写入时 embedding 已计算

### M6 — Leaky bucket ≤ 50 new cluster/day 未强制（与 ARCH-2 联动）

- **位置**: `pke/identity/resolver.py:107-128`
- **修复**: `_create_skill` 写入前 check `count(*) FROM skill_nodes WHERE created_at >= date('now')`；超 cap 入 `pending_audits.kind='leaky_bucket_block'`；ARI 触发的动态 cap（ARCH-2）从 `intervention_state`-like 全局状态表读

### M7 — Drift metrics 永不计算/写入（ARCH-2 已规划，此条 close-out）

### M8 — Bitemporal edge 字段命名偏离 spec（与 ARCH-3 一并）

### M9 — Maintenance jobs 全是 9-16 行 SQL counter stub

- **位置**: `pke/maintenance/jobs/{reembed,distill,decay,audit_split,audit_merge}.py`
- **修复**:
  - `reembed.py`: 真的 batch re-embed (1000 / batch) 所有 `embedding_model_version != current` 的 skill
  - `distill.py`: 真的训练 cross-encoder distillation 到 ms-marco-MiniLM-L-6-v2（spec §15）—— 但条件是 ≥ 5000 labeled pairs，没攒够直接 skip log "insufficient labels"
  - `decay.py`: 真的对每个 skill `unaided_retrievability *= exp(-Δt/halflife)` 而不是全局 flat 减
  - `audit_split.py / audit_merge.py`: 真的从 `pending_audits` 拉、按 confidence 排，high confidence 自动执行，low confidence 等人审

### M10 — Merge/split mastery transfer 未实现

- **位置**: 创建 `pke/mastery/transfer.py`
- **修复**: `merge_mastery(A, B) -> Merged`：`stability=min`, `difficulty=max`；`split_mastery(A) -> (A', B')`：duplicate state；接到 audit_split/merge jobs

### M11 — Parent/child Anderson spreading activation 未实现

- **位置**: `pke/maintenance/jobs/decay.py`
- **修复**: 每次 update mastery 后，沿 hierarchy 上传 child→parent α=0.4，下传 parent→child α=0.7；递归终止条件：abs(Δ) < 0.01

### M12 — Anti-annoyance 三件套未持久化（ARCH-4 已规划，此条 close-out）

### M13 — §4.6 所有 4 种 adapter UI 不接通

- **位置**: `pke/intervention/decider.py`（render_socratic_block / openai_system_prefix / anthropic_system_append 全是 dead code）；`pke/adapters/claude_code_hook.py`；`pke-ext/content-main.js`；`pke/adapters/openai_proxy.py`；`pke/adapters/anthropic_proxy.py`
- **修复**:
  - Claude Code hook 在 PostToolUse / UserPromptSubmit handler 中 call `decider.should_intervene` 命中则 stdout 打印 `<pke-socratic>...</pke-socratic>` block
  - Browser ext: content-main 注入 inline tooltip near chat input (`<div id="pke-tooltip">...</div>`)
  - OpenAI proxy: 把 socratic prefix 加到 system message
  - Anthropic proxy: 同上

### M14 — `/api/v1/review/start` bypass ItemSelector

- **位置**: `pke/web/routes/api_review.py:25-28`
- **现状**: unsorted `LIMIT N`
- **修复**: 改成 call `ItemSelector.select(user_id, k=5)`

### M15 — Selector 公式不符合 spec §5.2

- **位置**: `pke/mastery/selector.py:32-60`
- **现状**: `forgetting_term` 用 `unaided_retrievability`；`novelty_term` 缺失被 `pressure` 替代；cluster 多样性缺失
- **修复**: 改成 spec §5.2 公式 `score = w1 * (1 - p_recall) + w2 * (1 - unaided_mastery) + w3 * outsource_recency + w4 * novelty`；novelty = `1 / (1 + reviews_count)`；选 top-K 时强制至少 ⌈K/2⌉ distinct cluster

### M16 — §5.7 calibration 14-day trend 未渲染

- **位置**: `pke/web/templates/calibration.html`
- **修复**: 后端聚合过去 14 天 calibration delta → 折线图；趋势箭头（improving / stable / regressing）；中性 phrasing（"Your predictions were within X% on average"），不评判

### M17 — 10 个 adapter 都不实现 InputAdapter Protocol

- **位置**: `pke/adapters/base.py`（Protocol dataclass）+ 全部 10 个 adapter
- **修复**: 每个 adapter 类实现 `start() / stop() / events() -> Iterable / health() / backfill(since)`；删 `Protocol dataclass` 的 dead 字段；test 检查每个 adapter 都通过 isinstance check

### M18 — Claude Code Hook 角色错误 + session 不合并

- **位置**: `pke/adapters/claude_code_hook.py:90-126`
- **修复**: PostToolUse 第一 turn 用 `TOOL_CALL` role，第二 turn 用 `TOOL_RESULT`；增加 session merging（同 `session_id` 的 hook 事件归并）；启动时 drain `~/.local/share/pke/hook_buffer/` 的离线缓冲

### M19 — Browser ext 缺 XHR patch + claude.ai 不抓 + SSE raw

- **位置**: `pke-ext/content-main.js:1-47`
- **修复**:
  1. 同时 monkey-patch `XMLHttpRequest.prototype.send`
  2. 加 fetch URL pattern `claude.ai/api/organizations/*/chat_conversations/*/completion`
  3. SSE 按 spec parse 每个 `data:` 行 + reassemble `delta` 流，不要塞 raw body

### M20 — 两个 HTTP proxy 缓冲全部 body（streaming 不通）

- **位置**: `pke/adapters/{openai,anthropic}_proxy.py`
- **修复**: 用 `httpx.AsyncClient.stream("POST", ...)` + `async for chunk in r.aiter_bytes()` 真 pass-through；同时 fork 一份到内部 buffer 做 evidence 抽取；`external_id` 改成 `request_id` header（OpenAI 有，Anthropic `request-id`），不要含 timestamp 或 response 前缀

### M21 — watchdog 未 import；tailer 和 file-watcher 都是一次性扫描

- **位置**: `pke/adapters/claude_code_tailer.py`、`pke/adapters/file_watcher.py`
- **修复**: `from watchdog.observers import Observer` + `FileSystemEventHandler` 真实时监听；tailer 持久化 offset 到 `~/.local/share/pke/tailer_offsets.json` 处理 file rotation；inbox 默认 `~/PKE/inbox/` 不存在时 mkdir

### M22 — Migration runner 不写 schema_version

- **位置**: `pke/db/migrate.py:50-58`
- **修复**: 每个 migration apply 完写入 `schema_version (version INT PK, applied_at TS, file_hash TEXT)`；启动时校验顺序

### M23 — 19 个 test，无 hypothesis，无真 snapshot

- **修复**:
  - `tests/property/test_identity_stability.py` 用 hypothesis 测：随机 shuffle evidence stream → ARI ≥ 0.7
  - `tests/property/test_mastery_monotonicity.py` 用 hypothesis 测：连续 `polarity=demonstrated` 输入 → unaided mastery 单调上升（直到饱和）
  - `tests/snapshot/test_prompts.py` 每个 prompt rendered 后跟 `tests/snapshot/prompts/*.txt` 比对（首次跑生成 golden file）
  - 覆盖率目标：identity / mastery / extraction ≥ 80%；整体 ≥ 60%

### M24 — Dashboard 等模板大量硬编码占位

- **位置**: `pke/web/templates/{dashboard,today,settings,onboarding,skill_detail}.html`
- **修复**: 删 hardcoded "Async context managers" 之类示例；从后端 inject real data；`●●●○○` 改成 jinja 渲染基于 5-band mastery enum

### M25 — 5-band mastery enum 不存在；统一 glyph

- **位置**: enum 不存在
- **修复**: 新建 `pke/mastery/bands.py`:
  ```python
  class MasteryBand(StrEnum):
      UNSEEN = "unseen"
      ENCOUNTERED = "encountered"
      PRACTICING = "practicing"
      FAMILIAR = "familiar"
      FLUENT = "fluent"

  def to_band(unaided: float) -> MasteryBand:
      if unaided == 0: return MasteryBand.UNSEEN
      if unaided < 0.2: return MasteryBand.ENCOUNTERED
      if unaided < 0.5: return MasteryBand.PRACTICING
      if unaided < 0.8: return MasteryBand.FAMILIAR
      return MasteryBand.FLUENT
  ```
  模板里用 `{{ band.value }}` + 对应 glyph 映射

### M26 — i18n locale 各 3 个 key；模板全英文

- **位置**: `pke/web/i18n/{en,zh-CN}.json`
- **修复**: 把所有 template literal 抽出来生成 ~150-300 个 i18n key；写一个 lint 脚本检查 template 中无裸字符串；settings 加 locale switch

### M27 — Browser ext popup strength 改动不上传

- **位置**: `pke-ext/popup.js`
- **修复**: 改 storage 时同时 POST `/api/v1/settings/intervention_strength`；server 写 `intervention_state.override_strengths_json`（ARCH-4）

### M28 — reembed / distill 是 row count stub

- 同 M9 修复

---

## MINORS（不阻塞、不紧急）

- **MIN-1** CLI 单文件 384 行不是 §7.1 list 的 10 个 sub-file。功能齐全，仅 cosmetic。可在 finalize 前重构。
- **MIN-2** `/api/v1/evidence` 路由在 `pke/adapters/browser_ext_endpoint.py` 而非 `pke/web/routes/api_evidence.py`。挪过去 + import 调整。
- **MIN-3** `docs/philosophy.md` 缺失；`docs/install.md` 9 行、`docs/index.md` 4 行。补 5-10 页 docs。
- **MIN-4** Settings 不用 pydantic-settings（虽 declared）；只支持 1 个 env override。改用 pydantic-settings 完整 env override pattern。
- **MIN-5** `pke-ext/icons/` 空目录；MV3 manifest 声明 icon 但无文件。加 16/48/128 PNG（可用 emoji-to-png 或简单文字 logo）。
- **MIN-6** pyproject 大量未 import 依赖（kuzu / hnswlib / river / igraph / bertopic / scipy / sklearn / torch / watchdog / structlog / tenacity / orjson / platformdirs / tomli-w / pydantic-settings）。在所有 BLOCKER 修完后保留真用上的，删未用的。**警告**: 不要先删了再说，等真实现完成后核对。
- **MIN-7** 5 个 broad `except Exception:` —— 可接受（best-effort fallback），但加 log.exception。
- **MIN-8** `pick_item_type` deterministic ladder 从不生成 `explain-back`；改成 spec §5.3 随机化 banding（按距离选择策略）。
- **MIN-9** Symbolic grader 无 allowlist + 无 network disable。加 `~/.config/pke/grader_allowlist.toml` + subprocess 跑时 `--no-net`（用 unshare/sandbox-exec）。
- **MIN-10** ChatGPT history importer 静默丢 tool_calls / function_call；Claude.ai 丢 attachments / tool_use。log 警告 + counter 写入 `import_stats`。
- **MIN-11** Cursor SQLite 用 `immutable=0`；改 `=1`（read-only 性能 + 安全）。
- **MIN-12** Manual CLI 无 `--conversation-id` flag；server 不可达不 `exit 1`。两个都修。
- **MIN-13** File watcher 路由靠文件名子串匹配；改成 sniff 文件头（首 1KB 判断是 ChatGPT export / Claude.ai export / 其它 srt 等）。
- **MIN-14** Anthropic proxy 硬编码 `app="claude_code"`；改成从 `User-Agent` header 或 caller config 读。
- **MIN-15** Spec polarity vocab (`asked-about`) 与 SQL CHECK (`asked`) 不一致 —— `POLARITY_TO_EVIDENCE_KIND` 桥接可接受，但加注释指 spec §3 这是 known mismatch。
- **MIN-16** `pke/extraction/cache.py` 本地 disk KV 缓存 —— spec 没要求；保留但写文档说明它是 prompt-output cache，**不替代** Anthropic prompt caching。

---

## 完成判据

回到 `~/pke_spec.md` 第 §12 节验收清单（约 60 项）。在重新跑 audit workflow 后，要求达到：

- **60% 以上 ✅**（目前估计 < 25% pass）
- **0 个 BLOCKER 标 ❌**
- **不超过 5 个 MAJOR 标 ❌**
- **MINORS 可以剩**

audit workflow 复审 prompt：参考 `~/sediment_audit.md` 顶部 `COMMON_INSTRUCTIONS` + 6 个 audit agent prompt（结构 / Layer 1-2 / Layer 3-4 / Layer 5-6+algorithms / Adapters / Output+UX）。

## 已知 Codex 倾向（防范清单）

复审时检查 Codex 有没有重复以下"形似神空"模式：

1. ✅ 库 pin 在 pyproject 但代码不 import（再次出现立刻 fail）
2. ✅ `max(x, threshold)` 让 test 通过
3. ✅ 硬编码 f-string 替代 LLM call
4. ✅ `del rubric` / `_ = arg` 显式忽略 spec 参数
5. ✅ daemon / scheduler / job 是 print 后退出
6. ✅ static asset 是几十字节的 stub（`window.htmx = ...`）
7. ✅ Protocol/interface 写了但没 class 实现
8. ✅ `import` fail 时静默 fallback 到完全不等价的 path
9. ✅ Schema 定义对了但 caller 永远不 INSERT
10. ✅ CI 假装跑测试但实际 ImportError 在第一行

每条复审都明确 check 一遍。

---

**文档结束。** 修这些项的过程中如需澄清，在 `BLOCKER.md` 末尾追加 `## QUESTIONS` 章节，每条带 ID + 你的倾向 + 影响范围，**不要主动 ping 用户**。

## QUESTIONS

### Q-1: B20/B8 FSRS dependency name/version is unsatisfiable on PyPI
- 影响：B20 CI 真 `uv sync --all-extras`；B8 py-fsrs 接入。
- 你的倾向：选 A：继续用 `fsrs>=4,<5` 作为最接近 spec 的可安装 4.x 包；`py-fsrs` 是项目名，但 PyPI/module 名是 `fsrs`，且 `uv add 'fsrs>=4.5,<5'` 实测无解（只有 `<4.5` 或 `>=5`）。
- 默认动作（如果用户不答）：PR-2 用 `fsrs==4.1.2` 的真实 `Scheduler` / `Card` / `Rating` API 删除 toy scheduler；后续若上游发布 4.5.x 再收紧 pin。
