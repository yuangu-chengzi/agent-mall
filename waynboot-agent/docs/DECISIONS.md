# 设计决策记录（ADR）

> 格式：**决策 / 背景 / 备选方案 / 理由 / 影响**。新决策追加在文末，不修改历史条目。

---

## D1 · Agent 采用 LangGraph（Python 独立服务），而非 Spring AI 内嵌

- **背景**：mall 是 Java（Spring Boot 3.5.14）项目；上游作者在 `docs/AI_GUIDE_AGENT_DESIGN.md` 中设计了 Spring AI 内嵌方案。
- **备选**：① Spring AI 内嵌（作者方案，Java）② LangGraph 独立服务（Python）③ 先 ① 后 ②。
- **理由**：本项目的学习目标是 **LangChain/LangGraph 深度实践** + 后端工程能力双线；独立服务能形成清晰的"决策（LLM/编排）—执行（mall 业务）"边界，也更能体现跨服务协作能力。
- **影响**：必须定义工具 API 契约（见 `API_CONTRACT.md`）；需要两套运行环境；调 mall 走 HTTP（额外一跳网络开销，可接受）。

## D2 · 鉴权透传：Agent 原样携带用户的 Sa-Token 调 mall

- **背景**：Agent 需要以"当前用户"的身份查订单、加购物车。
- **备选**：① 服务账号 + 伪造用户上下文 ② 用户 token 透传 ③ Agent 自己签发 token。
- **理由**：② 语义最正确（mall 侧的鉴权、越权校验、审计全部自然生效），实现最简单（无需新增鉴权机制）。
- **影响**：Agent 不持有任何凭证；token 过期由 mall 返回 401 → Agent 转成"请重新登录"话术。匿名接口（商品/搜索）不带 token。

## D3 · 写操作必须 human-in-the-loop（LangGraph `interrupt`）

- **背景**：客服场景存在"发起退款""加购物车"等写操作。
- **理由**：LLM 存在幻觉与误判；写操作不可逆或影响资金。**决策与执行分离**的最高原则要求不可逆动作由人确认。
- **影响**：图需要在写工具节点前 `interrupt`，等待 `/api/chat/confirm` 恢复；需要会话状态持久化（Redis checkpointer）。

## D4 · 写操作带幂等键，防"连点确认"重复执行

- **背景**：用户可能在确认卡片上连点；网络重试也可能重复提交。
- **方案**：幂等键 = `sha256(session_id + ":" + action + ":" + 目标ID)`，执行前 `SETNX`（TTL 10 分钟），命中则直接返回上次结果。
- **理由**：mall 侧虽然也有幂等（购物车锁、券限领、订单状态机），但**在边界上先挡一层**更省资源、语义更清晰。
- **影响**：需要"动作结果"短暂缓存（Redis，同 key 前缀）。

## D5 · 会话记忆用 Redis（独立 key 前缀）

- **背景**：多轮对话需要状态；LangGraph 支持多种 checkpointer。
- **方案**：Redis checkpointer，key 前缀 `waynboot:agent:`（遵循 mall 的 key 命名规范），会话 TTL 24 小时。
- **理由**：复用 mall 的 Redis 实例（避免新增中间件）；前缀隔离避免与业务 key 混淆。
- **影响**：本地需 Redis 可用（与 mall 共用）；生产需评估会话数据量与内存。

## D6 · 知识库先用静态 FAQ，不引向量库（RAG 留作可选）

- **背景**：客服需要回答退换货政策、发票、运费等静态问题。
- **理由**：复用优先、成本优先。FAQ 只有几十条时**关键词/整段注入**足够，向量检索属于过度设计。
- **影响**：`faq` 工具先做关键词匹配；后续如需要再做向量化（可复用 mall 的 ES，注意其版本为 7.14，`dense_vector` 需 7.17+）。

## D7 · 测试不依赖真实 LLM 与网络

- **背景**：CI/本地都要能跑测试；LLM 调用有成本且不稳定。
- **方案**：图测试用自研 `ScriptedLLM`（只实现 `bind_tools`/`ainvoke`，见 `tests/fakes.py`）；
  HTTP 用 `httpx.MockTransport` 注入（`MallClient` 预留了 `transport` 参数）——**不 monkeypatch 全局、不模拟网络**。
- **理由**：**测试的边界是"可断言性"**——外部依赖的不确定性要在测试边界外隔离；同时呼应 mall 侧"测试测业务不变量"的规范。
- **影响**：需要为工具层与图节点设计可注入的接口（LLM、HTTP client 均通过依赖注入）。

## D8 · 失败降级：LLM/工具失败 → 明确话术 + 引导转人工

- **背景**：LLM 超时、限流、工具异常都可能发生。
- **方案**：① 工具失败 → 把错误信息作为 observation 回给 LLM（让它决定是否重试/换工具）；② 连续失败或达到步数上限 → 输出兜底话术 + `escalation` 事件；③ LLM 完全不可用 → 直接走静态话术 + 转人工。
- **理由**：**不静默失败**（mall 侧的"有观测、有处置"原则迁移到 Agent 侧）。
- **影响**：需要 `max_steps`、`tool_error_threshold` 等配置项；需要结构化事件（SSE）区分"话术/商品卡片/转人工"。

## D9 · 会话记忆用**自研精简 Redis checkpointer**，不用官方 `langgraph-checkpoint-redis`

- **背景**：P3 要给图接 Redis 会话记忆。官方包 `langgraph-checkpoint-redis` 的 `RedisSaver` / `AsyncShallowRedisSaver`
  都依赖 **RedisJSON + RediSearch**（`redisvl` 建立二级索引）。
- **事实**：本机 Redis 是 Windows 社区构建 `Redis 8.10.2`（D:\redis\redis-8.10.2），**未编译这两个模块**：
  实测 `JSON.SET` 与 `FT._LIST` 均返回 `unknown command`，`MODULE LIST` 只有 `vectorset`。
  Windows 上既没有官方 Redis，也没有 Docker/WSL 可用（两者本机都没装），换 Redis Stack 的成本高于收益。
- **备选**：① 装 Docker/WSL 跑 Redis Stack ② 自研只用 `SET/GET/EXPIRE` 的 checkpointer ③ 退回 `InMemorySaver`。
- **理由**：客服会话只需要"**每个 thread 的最新状态 + TTL**"（不需要官方 checkpointer 的完整历史/时间旅行/跨 thread 检索），
  ② 用 ~100 行即可覆盖，且**不引入 redisvl/numpy 这类重依赖**；官方包已从必需依赖移到可选 extra `stack`
  （换成 Redis Stack 环境时可直接替换实现）。
- **影响**：`app/memory/` 需自研 `BaseCheckpointSaver` 子类并测试（含 P4 `interrupt` 依赖的 pending writes）；
  官方包的完整能力（时间旅行、历史检索）不被支持——本项目不需要。

## D10 · LLM 默认 `deepseek-flash`；API Key 优先显式配置，回退系统环境变量

- **背景**：接入真实模型时发现 DeepSeek 当前可选模型为 `deepseek-flash` / `deepseek-v4-pro`
  （**没有** 旧的 `deepseek-chat`，实测 `GET /v1/models` 确认）。
- **方案**：`AGENT_LLM_MODEL` 默认 `deepseek-flash`（快、便宜，客服对话足够）；
  `llm_api_key` 用 `AliasChoices("AGENT_LLM_API_KEY", "DEEPSEEK_API_KEY")` ——
  没显式配置时**回退读系统环境里的 `DEEPSEEK_API_KEY`**。
- **理由**：密钥留在操作系统的环境变量存储里，**比复制一份到仓库的 `.env` 更安全**；同时本地零配置即可跑真模型。
- **影响**：`.env` 可以不写 key；CI/他人机器上没有该变量时自动回到降级话术路径。

## D11 · 个人数据工具做 fail-closed 所有权校验（应对上游 A15 越权缺陷）

- **背景**：mall 的 `GET /order/detail/{orderSn}` **不校验订单归属**（`OrderQuerySupport.java:165-166`），
  任意登录用户凭单号可读他人订单（收货人/手机号/地址）。Agent 恰好会被用户诱导去查任意单号。
- **方案**：`order_detail`/`logistics` 先确认"该单号出现在**当前用户**的订单列表里"（最多翻 3 页 × 50 笔），
  不通过就一律回"没找到"，**且不请求详情接口**。上游修复建议见 PLAN 的 A15。
- **理由**：Agent 不能成为**数据泄露放大器**——即使上游有洞，Agent 侧也要 fail-closed。
- **影响**：每次详情查询多 1 次列表请求；用户订单超过 150 笔且查更早的单时会被拒绝（已在话术里说明）。

---

## D12 · 限流按"用户身份"计数；Redis 故障时 fail-open

- **背景**：一个用户可以把当天额度刷爆（或脚本刷接口），既费钱也压垮下游 mall。
- **方案**：固定窗口计数（QPS 1 秒窗口 + 日配额 24 小时窗口）。身份＝``sha256(Authorization)`` 前 16 位，
  没带 token 就用客户端 IP。**凭证原文不进 Redis key**。命中即 429 + ``Retry-After`` + 友好话术。
- **理由**：与 mall 秒杀频控同一思路（复用认知与实现习惯）；固定窗口足够，滑动窗口要 ZSET 存时间戳属于过度设计。
- **fail-open**：Redis 不可用时**放行**并打警告。限流是保护措施，不能变成新的故障点——
  但"限流失效"必须留下痕迹，否则会悄悄失效。
- **影响**：探针接口（`/healthz`、`/readyz`）不挂限流，避免编排系统误判服务不可用。

## D13 · 任何降级都必须带回人工的出口（D8 的落地口径）

- **方案**：降级（LLM 未配置 / 调用失败 / 超出步数上限 / 无答复）时，``TurnResult.escalate`` 一并置 ``true``，
  一次性接口与 SSE 的 ``done`` 事件都带这个标记。
- **理由**：**降级话术本身就在说"建议转人工"**，但只给话术不给标记，前端无从判断该不该接人工——
  等于把"用户自己再打一遍字"的成本丢回给用户。
- **影响**：前端收到 `escalate=true` 时可以直接落工单/接入人工队列（P5 仅到"标记 + 记录日志"，
  真正的工单表属于后续可选项）。

## D14 · 会话线程键由「调用方身份 + session_id」派生，不直接用 session_id

- **背景**：`session_id` 由前端生成、用户可见，而它原本直接当 checkpointer 的 `thread_id`。
- **问题（实测）**：换一个 token、不换 session_id，就能读到别人的整段对话历史——
  里面可能有订单号、手机号、地址。**标识符被当成了凭证**，与 mall 侧的 A15/A16 同一类病。
- **方案**：`thread_id = sha256(token)[:16] + ":" + session_id`；匿名会话退化为 `anon:{session_id}`。
  会话查询/清空接口用同一套派生，保证"看到的和写的"是同一个线程。
- **理由**：身份是服务端从凭证推导的，用户碰不到别人的线程；改动集中在 `resolve_thread_id` 一处。
- **影响**：老会话（升级前写入的）读不到了，等价于清空（TTL 24h 内自然过期）；
  用户在会话中途重新登录会开启新会话（语义上也更正确）。

## D15 · 长对话用「滑动窗口 + 省略提示」，而不是摘要压缩

- **背景**：每轮把全量历史发给模型 → token 成本随轮数线性涨，几十轮后撞上下文上限直接报错。
- **备选**：① 滑动窗口 ② 摘要压缩（额外的 LLM 调用把早期消息压成一段）③ 向量检索历史。
- **理由**：① 零额外调用、确定性可测（摘要本身可能失真，且又引入一次 LLM 调用与成本）；
  配一条"更早的 X 条已省略"的提示，让模型知道上下文不完整、必要时反问用户——**比静默截断诚实**。
- **影响**：默认 24 条（`AGENT_HISTORY_MAX_MESSAGES`）；窗口从中间切开时要丢掉开头的孤儿
  `ToolMessage`，否则部分模型会因工具调用配对不完整报错。摘要方案留作后续（会话很长且必须记住早期信息时）。

## D16 · 用量按「身份 + 天」记账，身份与限流共用同一套派生

- **背景**：LLM 是这套系统最贵的资源，没有用量数据就没法谈成本（"这个月花了多少、谁花得最多"）。
- **方案**：`UsageCallback` 挂到图执行上（能捕获工具循环里的每一次调用）；`UsageTracker` 用
  `HINCRBY` 按天累加，保留 7 天；`GET /api/usage` 让调用方看自己的账。
- **理由**：回调比"读最后一条 AI 消息"准确（一轮可能调用多次）；身份与限流共用 `caller_identity`，
  否则"限流按用户、统计按 IP"两边对不上账。
- **影响**：统计失败**只打警告不抛异常**（观测手段不该成为故障点，与限流 fail-open 同一个判断）；
  模型不返回用量时记 0 次调用，不影响对话。

---

## D17 · 商城前端由 Agent 反向代理 mall，而不是给 mall 开 CORS

- **背景**：P8 要一套能在浏览器里跑通全流程的商城前端。页面由 Agent（8300）托管，
  业务接口在 mall（82），浏览器直接调 mall 会撞同源策略。
- **备选**：① 给 mall 加 CORS 放行 8300 ② 前端和 mall 同源部署（把静态文件塞进 mall）③ Agent 做反向代理（BFF）。
- **理由**：③ 改动最小且边界最清晰——mall 一行不动；浏览器只认一个源（调试、预览、抓包都简单）；
  顺带不用把 mall 暴露给浏览器。② 会把前端和 Java 工程耦合在一起，和 D1 的"两个服务"边界矛盾；
  ① 需要改 mall 的配置且放行了跨域来源，安全面反而更大（本项目已有登录代理，代理模式是既有做法的延续）。
- **影响**：`/api/mall/**` 是**白名单**代理（只放行商城页用到的前缀，见 `app/mall/proxy.py`），
  且**不挂 LLM 限流与访问闸门**——那两道闸门保护的是花钱的 LLM 额度，用户购物不该被咨询配额拦住。
  代理层不解 R 信封、不改状态码：前端看到的和直连 mall 完全一致，不引入第二套语义。

---

## D18 · 调 mall 的 HTTP 客户端显式关掉"系统代理"，LLM 客户端保持默认

- **背景**：联调时反复出现"直接 curl 通、Agent 却说不通"的怪事：`/readyz` 报 mall 不可达、图片接口 502。
- **根因**：httpx 默认 `trust_env=True`，会去读系统代理（Windows 上读注册表，本机有个 `127.0.0.1:33210` 的加速器）——
  于是连 `http://localhost:82` 这种本机请求也被塞进代理，代理自然够不着。
- **方案**：`MallClient` 的 `httpx.AsyncClient` 加 `trust_env=False`；**LLM 客户端不动**。
- **理由**：mall 是**同机/内网的兄弟服务**，正常部署下不该走代理；而 LLM 要访问公网，
  在需要代理才能出网的机器上恰恰**必须**走代理，一刀切会把它弄坏。两边诉求相反，就分开。
- **影响**：若将来把 Agent 和 mall 部署在"必须经代理才能互访"的环境（少见），需要给 mall 客户端单独开代理配置。

---

## 工程约定（非独立决策，随开发固定下来）

| 项 | 约定 |
|---|---|
| 配置 | `pydantic-settings` 读取 `.env`（前缀 `AGENT_`）；**key 不入库**（`.env.example` 只放占位） |
| 日志 | 一条日志一行、带 `request_id` 贯穿全链路（`[req=...]`）；mall 调用记录方法/路径/耗时/结果 |
| 工具命名 | snake_case 动词短语（`search_goods`、`order_detail`），与 API_CONTRACT 一致 |
| 工具返回 | 统一返回**给 LLM 读的精简文本**（不是 JSON）：`- ` 列表 + 括号内的"系统提示"；错误也返回文本（**不抛异常**） |
| 步数上限 | `AGENT_GRAPH_RECURSION_LIMIT`（默认 16，一轮"决策+执行工具"消耗 2 步 ≈ 8 轮工具调用），直接作为 LangGraph `recursion_limit` |
| 会话 TTL | `AGENT_SESSION_TTL_SECONDS`（默认 86400 = 24 小时） |
| 成本控制 | 每用户 QPS + 日配额；单次会话 token 上限（P5） |
