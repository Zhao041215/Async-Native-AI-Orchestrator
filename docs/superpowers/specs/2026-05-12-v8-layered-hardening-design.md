# V8 分层强化设计文档

**日期：** 2026-05-12  
**版本：** V8 → V8.1  
**优先级顺序：** 稳定性+效率（C+E）→ 大项目支持（B）→ 代码质量（A）→ 开箱即用（D）→ 自动返工（Rework）

---

## 背景与目标

当前系统（V8）已具备完整的多智能体并行开发流水线，但在以下方面存在明显不足：

| 问题类别 | 具体表现 |
|---------|---------|
| 稳定性（C） | Worker 崩溃后僵尸 job 无法自愈；LLM 返回截断 JSON 直接失败；多 worker 无跨进程并发协调 |
| 效率（E） | 中型项目（5000行，10-15包）耗时 30-60 分钟；cross-package context 注入全文浪费大量 token |
| 大项目（B） | 规模推断不准；包间接口不一致；大项目上下文超长 |
| 代码质量（A） | 占位符/TODO 未完成；依赖文件缺失；接口签名不一致 |
| 开箱即用（D） | 部署文档与实际代码脱节；无环境验证脚本 |

**目标：** 中型项目耗时从 30-60 分钟降至 15 分钟以内；消灭僵尸 job；大项目（>1万行）稳定完成；生成产物用户开箱即用。

---

## 第一层：稳定性 + 效率（C+E）

### 1.1 三层并发架构

**问题：** `AsyncAIScheduler` 的三级 `asyncio.Semaphore` 是进程内的。多 worker 进程时没有跨进程协调，实际 LLM 并发 = 配置值 × worker 数，provider 必然 429。`WorkerSupervisor` 代码存在但未真正实现多进程管理。

**架构：**

```
Layer 3: PostgreSQL ai_slots 全局槽池（跨进程）
  max_ai_slots = 8（全局，所有 worker 共享）
  每个 LLM 调用前 acquire，完成后 release
  槽有 TTL，worker 崩溃后自动过期

Layer 2: 进程内 asyncio.Semaphore
  local_concurrency = min(4, max_ai_slots)
  限制单个 worker 内同时发起的 LLM 调用数
  防止单 worker 独占全部全局槽

Layer 1: WorkerSupervisor 进程管理
  N 个 worker 进程（N 可配置，默认 2）
  每个 worker 独立 event loop
  通过 PostgreSQL jobs 表协调（已有 SKIP LOCKED）
```

**PostgreSQL ai_slots 全局槽池（原子 SQL）：**

```sql
-- acquire
INSERT INTO ai_slots (slot_id, worker_id, task_kind, expires_at)
SELECT gen_random_uuid(), $1, $2, now() + $3::interval
WHERE (SELECT COUNT(*) FROM ai_slots WHERE expires_at > now()) < $4
RETURNING slot_id;

-- release
DELETE FROM ai_slots WHERE slot_id = $1;

-- reaper
DELETE FROM ai_slots WHERE expires_at <= now();
```

槽 TTL 按 task_kind：`implementation` = 600s，`planning`/`architecture` = 300s，其他 = 180s。

**AsyncAIScheduler 改造：**
- 移除进程内 ai_slots 计数器
- `call()` 流程：进程内 semaphore → PostgreSQL slot（指数退避，最多等 120s）→ LLM 请求 → release slot → release semaphore
- slot acquire 超时不计入 LLM 重试次数，单独上报 `slot_timeout` 指标

**WorkerSupervisor 真正实现：**
- `asyncio.create_subprocess_exec` 启动 N 个 worker 子进程
- 每个子进程：`python -m dev_orchestrator.v8.worker --worker-id=<uuid> --worker-index=<n>`
- 崩溃后自动重启（最多 3 次/小时，超过则停止并告警）
- Supervisor 后台协程：`reaper`（每 60s 清理过期 ai_slots 和僵尸 jobs）、`health_monitor`（每 30s 检查子进程存活）

**Adaptive Throttling（进程内）：**

滑动窗口（最近 20 次调用）计算 p95 响应时间：

| 条件 | 动作 |
|------|------|
| p95 > 30s | local_concurrency -= 1（最低 1） |
| p95 < 10s 且连续 10 次 | local_concurrency += 1（最高不超过 max_ai_slots / worker_count） |
| 收到 429 | 立即降到 1，等待 Retry-After 或默认 60s |
| 收到 5xx 连续 3 次 | 触发 circuit breaker |

**配置说明：** `max_ai_slots`（默认 8）和 `worker_count`（默认 2）均可在 scale profile 中覆盖。enterprise 档位建议 `max_ai_slots=16, worker_count=4`。

**涉及文件：** `scheduler.py`、`worker.py`、`store_pg.py`、`migrations/001_v8_initial.sql`

---

### 1.2 Heartbeat 自愈机制

**问题：** Worker 崩溃后 job lease 永远不超时，变成僵尸 job。`store.heartbeat_job()` 存在但未被调用。

**设计：**
- `AsyncWorker` claim job 后启动后台 Task，每 30s 调用 `store.heartbeat_job()`
- job 完成/失败时取消 heartbeat task
- lease 时长按 task_kind 动态设置（与 ai_slots TTL 对齐）
- `WorkerSupervisor.reaper` 每 60s 调用 `store.reset_stale_jobs()`，将 `lease_expires_at < now()` 的 running job 重置为 pending

**涉及文件：** `worker.py`、`store_pg.py`

---

### 1.3 LLM 响应健壮性

**问题：** LLM 返回截断 JSON 时直接抛异常触发完整重试。circuit breaker 无法区分"模型返回垃圾"和"网络超时"。

**新增 `json_repair.py`：**
- 修复未闭合 `{`/`[`：补全对应闭合符
- 修复末尾截断字符串：截断到最后一个完整字段
- 修复尾部乱码：正则提取第一个完整 JSON 对象
- 修复失败才计入重试次数

**`resilience.py` 错误分类扩展：**

| 错误类型 | circuit breaker 计数 |
|---------|---------------------|
| 网络超时、HTTP 5xx、429 | 是 |
| `MALFORMED_RESPONSE`（非法 JSON） | 否，仅记录 |
| `CONTRACT_VIOLATION`（JSON 合法但不符合契约） | 否，仅记录 |

**涉及文件：** 新增 `json_repair.py`，修改 `llm_client.py`、`resilience.py`

---

### 1.4 Prompt Caching

**设计：**
- 系统提示超过 1024 tokens 时，在消息数组中插入 `cache_control: {"type": "ephemeral"}` 标记
- 兼容 Anthropic prompt caching；对 OpenAI 兼容接口无副作用
- 缓存命中率记录到 `AgentRun.cache_hit` 字段

**涉及文件：** `llm_client.py`、`models.py`

---

### 1.5 Token 压缩：结构化 Cross-Package Context

**问题：** `_build_cross_package_context()` 注入完整 patch_set 内容，大项目时单个 prompt 可达 30k+ tokens。

**新增 `context_summarizer.py`，两级摘要：**

| 级别 | 内容 | 约 tokens |
|------|------|-----------|
| L1 | 包名、角色、导出符号列表、文件路径 | ~100 |
| L2 | L1 + API 端点、数据模型字段、关键依赖 | ~400 |

**`implementation.py` 按依赖图距离注入：**
- 直接依赖包 → L2 摘要
- 间接依赖包（距离 2）→ L1 摘要
- 无依赖关系包 → 不注入
- 总 context 预算上限：`max_input_chars × 0.4`

**涉及文件：** 新增 `context_summarizer.py`，修改 `implementation.py`

---

## 第二层：大项目支持（B）

### 2.1 智能规模推断

**问题：** `scale_inference.py` 用简单关键词匹配推断规模，复杂项目容易被判成 medium。

**多维评分模型（每维 0-3 分）：**

| 维度 | 信号 | 权重 |
|------|------|------|
| 功能点密度 | 需求描述中的动词短语数（CRUD、API、业务流程） | 30% |
| 技术栈复杂度 | 多服务架构关键词（微服务、消息队列、缓存、多DB） | 25% |
| 集成复杂度 | 第三方服务数量（支付、邮件、OAuth、存储） | 20% |
| 非功能需求 | 高并发、高可用、多租户、国际化等关键词 | 15% |
| 描述长度 | 需求文本字数 | 10% |

评分 → 规模：0-2=small，2-4=medium，4-6=large，6-8=xlarge_100k，8+=xxlarge_300k

推断结果写入 `run.metadata.scale_inference_detail`（含各维度得分）。

**涉及文件：** `scale_inference.py`

---

### 2.2 跨包接口契约层

**问题：** 各包独立生成代码，没有共享接口定义，导致包间 API 签名、数据模型字段不一致。

**新增 `ContractPhase`（在 planning 和 implementation 之间）：**
- 调用 LLM 基于架构文档生成 `interface_contracts.json`，包含：
  - 所有跨包 API 端点（路径、方法、请求/响应 schema）
  - 共享数据模型（字段名、类型、必填性）
  - 数据库表结构（表名、列、外键）
  - 事件/消息格式（如有消息队列）
- 契约文件写入 `workspace/projects/<id>/contracts/interface_contracts.json`
- 同时存为 artifact，供后续阶段引用

**`implementation.py` 引用契约：**
- 代码生成 prompt 注入与当前包相关的契约片段
- 系统提示强制约束：必须严格遵守 interface_contracts 中定义的接口签名，不得自行修改

**`integration.py` 验证契约（新增 ContractValidator）：**
- 从生成代码中提取实际符号（函数签名、路由定义）
- 与契约对比，不匹配项作为 `critical` 级别问题上报，触发 blocked

**涉及文件：** 新增 `phases/contract.py`，修改 `implementation.py`、`integration.py`、`pipeline.py`、`models.py`

---

### 2.3 增量滚动上下文

**问题：** 大项目（>20包）时，所有已完成包摘要全部注入，prompt 超长。

**设计：**
- `context_summarizer.py`（第一层已引入）扩展为两级摘要（L1/L2，见 1.5）
- `implementation.py` 按依赖图距离注入（见 1.5）
- 大项目（包数 > 20）额外启用"波次隔离"：只注入同 wave 或前一 wave 的包摘要，更早的 wave 仅注入 L1

**涉及文件：** `context_summarizer.py`、`implementation.py`

---

## 第三层：代码质量（A）

### 3.1 完整性门控（即时重试）

**问题：** 代码生成后的质量检查在 QualityPhase 才运行，重试代价高。

**设计：**
- `implementation.py` 收到 LLM 响应后、写文件前，运行 `CompletenessChecker`：
  - 扫描检测：`TODO`、`FIXME`、`pass\n`（Python）、`throw new Error("not implemented")`（JS）、`NotImplementedError`、空函数体
  - 检测文件是否为空或仅有注释
  - 占位符密度 > 10%（占位符行数/总行数）则不合格
- 不合格时在同一 job 内立即重试，重试 prompt 附加：`"上一次生成包含以下未完成内容，必须全部实现：<占位符列表>"`
- 最多重试 2 次，仍不合格则标记 `quality_warning` 但不阻塞

**涉及文件：** 新增 `completeness_checker.py`，修改 `implementation.py`

---

### 3.2 依赖文件强制生成

**问题：** infrastructure 包应生成依赖文件，但没有强制约束，AI 可能遗漏。

**`planning.py` 的 `_ensure_infrastructure_package()` 增强：**
- 根据技术栈在 `allowed_paths` 中强制包含对应依赖文件：
  - Python → `requirements.txt`, `pyproject.toml`
  - Node.js → `package.json`, `package-lock.json`
  - Go → `go.mod`, `go.sum`
  - Java → `pom.xml` 或 `build.gradle`
- 这些路径同时加入 `required_outputs`

**`QualityPhase` 增加 `DependencyGate`：**
- 检查 `required_outputs` 中的依赖文件是否实际存在于 workspace
- 检查依赖文件内容是否非空、格式合法（JSON/TOML/YAML 可解析）
- 失败则触发 `dependency_fix` job：专门重新生成缺失的依赖文件

**涉及文件：** `planning.py`、`phases/quality.py`，新增 `dependency_gate.py`

---

### 3.3 接口一致性验证

由第二层 2.2 的 `ContractValidator` 覆盖，不重复设计。

---

## 第四层：开箱即用（D）

### 4.1 部署文档精确化

**问题：** `release.py` 的 LLM 没有看到实际生成的文件列表，部署文档可能与实际代码不符。

**`release.py` 的 `user_payload` 增加：**
- `generated_files`：所有 patch_set artifact 中的文件路径列表
- `dependency_files`：依赖文件内容（requirements.txt 等，截取前 100 行）
- `detected_stack`：从 scale_inference 提取的技术栈信息
- `entry_points`：从代码中识别的入口文件（main.py、index.js、cmd/main.go 等）

**涉及文件：** `phases/release.py`、`deploy_doc.py`

---

### 4.2 环境验证脚本

**`deploy_doc.py` 在生成 `start.sh`/`start.bat` 的同时，生成 `validate.sh`/`validate.bat`：**
- 检查运行时版本（python --version、node --version 等）
- 检查必要端口是否可用
- 检查环境变量是否已设置
- 检查依赖是否已安装（pip check、npm ls 等）
- 输出 PASS/FAIL 报告，FAIL 时给出修复建议

**涉及文件：** `deploy_doc.py`

---

## 第五层：自动返工机制（Rework Loop）

### 5.1 整体流程

在 QualityPhase 和 ReleasePhase 之间插入 Rework 决策节点：

```
QualityPhase
    ↓ 发现问题
ReworkOrchestrator（新增）
    ↓ 问题 → 包映射
fix_implementation jobs（只针对有问题的包）
    ↓ 修复完成
IntegrationPhase（局部重跑）
    ↓
QualityPhase（再次验证）
    ↓ 通过 or 超出返工预算
ReleasePhase
```

### 5.2 问题 → 包映射

每个 `GateResult` 记录问题时附带 `affected_files`（已有字段）。`ReworkOrchestrator` 通过文件路径反查 `WorkPackage.allowed_paths` 确定责任包：

| 问题类型 | 定位方式 |
|---------|---------|
| 文件缺失 | 从 `required_outputs` 反查应生成该文件的包 |
| 接口不一致 | 找到实现该接口的包（通过契约层反查） |
| 代码错误（占位符/语法） | 找到包含该文件的包 |
| 依赖文件缺失 | 定位到 infrastructure 包 |

### 5.3 Surgical Fix Job

为每个有问题的包创建 `fix_implementation` 类型的 job。

Fix prompt 额外注入：
- 本包在质量检测中发现的具体问题列表
- 约束：只修复上述问题，不要修改其他已通过检测的文件
- 当前文件的实际内容（让 AI 看到现有代码再修）

Fix job 只覆盖有问题的文件，不触碰其他文件（通过 `allowed_paths` 限制为问题文件列表）。

### 5.4 返工预算（防死循环）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_rework_iterations` | 2 | 每个 run 最多返工轮数，可在 scale profile 中配置 |
| 单轮返工包数上限 | 总包数 × 50% | 超过则判定为系统性问题，不适合局部修复 |
| 单包最大返工次数 | 1 | 防止单包无限循环 |

第 2 轮仍有问题：标记 `quality_warning` 但放行到 release，在部署文档中列出已知问题。

返工历史记录在 `run.metadata.rework_history`（含每轮问题列表、修复包列表、结果）。

**涉及文件：** 新增 `phases/rework.py`，修改 `pipeline.py`、`models.py`、`profiles.py`

---

## 新增文件汇总

| 文件 | 所属层 | 用途 |
|------|--------|------|
| `dev_orchestrator/v8/json_repair.py` | 第一层 | JSON 截断修复工具 |
| `dev_orchestrator/v8/context_summarizer.py` | 第一层/第二层 | 两级包摘要生成 |
| `dev_orchestrator/v8/completeness_checker.py` | 第三层 | 代码完整性扫描 |
| `dev_orchestrator/v8/dependency_gate.py` | 第三层 | 依赖文件存在性验证 |
| `dev_orchestrator/v8/phases/contract.py` | 第二层 | 跨包接口契约生成 |
| `dev_orchestrator/v8/phases/rework.py` | 第五层 | 自动返工编排 |

---

## 修改文件汇总

| 文件 | 修改内容 |
|------|---------|
| `scheduler.py` | 移除进程内 ai_slots 计数器；接入 PostgreSQL 全局槽池；Adaptive Throttling |
| `worker.py` | Heartbeat Task；WorkerSupervisor 真正实现（多进程启动、监控、重启） |
| `store_pg.py` | ai_slots acquire/release/reaper SQL；reset_stale_jobs 定期调用 |
| `llm_client.py` | json_repair 集成；prompt caching 标记注入 |
| `resilience.py` | 新增 MALFORMED_RESPONSE / CONTRACT_VIOLATION 错误类型 |
| `scale_inference.py` | 多维评分模型替换简单关键词匹配 |
| `implementation.py` | 引用契约层；context_summarizer 替换全文注入；completeness_checker 集成 |
| `integration.py` | ContractValidator 集成；文件预览从 500 字符扩展到 2000 字符（覆盖更多逻辑）；修复 `except Exception: pass` 异常吞噬 |
| `planning.py` | _ensure_infrastructure_package 增强依赖文件强制声明 |
| `phases/quality.py` | DependencyGate 集成 |
| `phases/release.py` | user_payload 增加 generated_files / dependency_files / detected_stack / entry_points |
| `deploy_doc.py` | 生成 validate.sh / validate.bat |
| `pipeline.py` | 插入 ContractPhase；插入 ReworkOrchestrator 节点 |
| `models.py` | 新增 ReworkHistory、ContractViolation、scale_inference_detail 等字段 |
| `profiles.py` | 新增 max_rework_iterations 配置项 |
| `migrations/001_v8_initial.sql` | 激活 ai_slots 表；新增 rework_history 字段 |

---

## 实施顺序

```
第一层（稳定性+效率）
  1. migrations: 激活 ai_slots 表
  2. store_pg.py: ai_slots SQL 操作
  3. json_repair.py: 新增
  4. resilience.py: 错误分类扩展
  5. llm_client.py: json_repair 集成 + prompt caching
  6. scheduler.py: 全局槽池 + Adaptive Throttling
  7. worker.py: Heartbeat + WorkerSupervisor 多进程
  8. context_summarizer.py: 新增
  9. implementation.py: context_summarizer 替换全文注入

第二层（大项目支持）
  10. scale_inference.py: 多维评分模型
  11. phases/contract.py: 新增 ContractPhase
  12. pipeline.py: 插入 ContractPhase
  13. implementation.py: 契约引用
  14. integration.py: ContractValidator + 修复异常吞噬 + 扩展预览

第三层（代码质量）
  15. completeness_checker.py: 新增
  16. implementation.py: completeness_checker 集成
  17. dependency_gate.py: 新增
  18. planning.py: 依赖文件强制声明
  19. phases/quality.py: DependencyGate 集成

第四层（开箱即用）
  20. phases/release.py: user_payload 增强
  21. deploy_doc.py: validate 脚本生成

第五层（自动返工）
  22. models.py: ReworkHistory 等新字段
  23. profiles.py: max_rework_iterations
  24. phases/rework.py: 新增 ReworkOrchestrator
  25. pipeline.py: 插入 Rework 节点
```
