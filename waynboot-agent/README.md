# waynboot-agent · 客服 Agent 服务

> 为 [waynboot-mall](../) 商城后端配套的 **AI 客服 Agent 服务**：用 **LangChain + LangGraph** 实现多轮对话、工具调用、人工确认与转人工的完整客服闭环。
>
> 状态：**P0–P5 已完成**（骨架 / 最小图 / 只读工具 / 会话记忆 + SSE / 写操作人工确认 / 收尾）。
> 进度与验证缺口见 [docs/PLAN.md](docs/PLAN.md)，关键决策见 [docs/DECISIONS.md](docs/DECISIONS.md)。

---

## 1. 它解决什么问题

商城后端的客服场景需要回答/处理这类请求：

| 用户说 | Agent 要做 |
|---|---|
| "我的订单到哪了？" | 查订单列表 → 定位订单 → 读物流字段 → 组织话术 |
| "这个商品还有货吗？尺码怎么选？" | 查商品详情（含 SKU 库存/规格）→ 回答 |
| "我要退货，怎么操作？" | 查订单状态判断是否符合政策 → 解释流程 →（可选）发起退款申请 |
| "我领的券怎么用不了？" | 查用户券 → 比对使用门槛/有效期 → 解释 |
| "转人工" | 触发转人工（escalation） |

**边界（不做）**：改价、直接下单、跨用户数据访问、开放域闲聊。

## 2. 架构

```
┌──────────────────────────┐
│  前端 / 调试脚本 / 测试    │
└────────────┬─────────────┘
             │ HTTP (SSE 流式)
┌────────────▼────────────────────────── waynboot-agent (Python) ─────────────┐
│  FastAPI 入口层                                                              │
│    ├─ 鉴权：透传用户 Authorization（Sa-Token）→ 调用 mall 时原样携带          │
│    └─ 限流/配额：每用户 QPS + 日配额（复用 mall 秒杀频控思路）                │
│  ─────────────────────────────────────────────────────────────────────────  │
│  LangGraph 编排层                                                            │
│    StateGraph:  agent(LLM) ──▶ tools ──▶ agent ──▶ …（recursion_limit 封顶） │
│                │                    │                                        │
│                │                    └─ 写操作 → interrupt() 等待人工确认      │
│                └─ 会话状态 → Redis Checkpointer（复用 mall 的 Redis）        │
│  ─────────────────────────────────────────────────────────────────────────  │
│  工具层（LangChain @tool）                                                   │
│    ├─ 只读：search_goods / goods_detail / my_orders / order_detail /         │
│    │        logistics / my_coupons / faq                                     │
│    └─ 写操作（需确认）：request_refund / add_to_cart                          │
└────────────┬─────────────────────────────────────────────────────────────────┘
             │ HTTP（R{code,msg,data} 契约）
┌────────────▼───────────────────────────┐
│  waynboot-mobile-api (Java, 端口 82)    │  ← 复用现有接口，零改动（除必要修复）
│   商品 / 订单 / 优惠券 / 退款 / 地址 …    │
└─────────────────────────────────────────┘
```

### 五条设计原则

1. **决策与执行分离**（最高原则）：LLM 只产出"决策"（调哪个工具、说什么），**所有真实读写走 mall 现有接口**——LLM 的不确定性永远碰不到订单/库存/支付核心链路。
2. **只读自由、写操作确认**：只读工具 Agent 自主调用；写操作（发起退款、加购）必须**中断等待用户确认**（LangGraph `interrupt` + human-in-the-loop）。
3. **可控循环**：`recursion_limit` 封顶步数；LLM/工具失败时**降级**为明确话术，不静默失败。
4. **复用优先**：不引向量数据库、不引重型框架；记忆复用 mall 的 Redis；知识库先用静态 FAQ 文档（RAG 作为后续可选）。
5. **写操作幂等**：所有写操作携带幂等键（会话 ID + 动作指纹），复用 mall 侧已有的幂等设计，避免"用户连点确认"造成重复。

## 3. 快速开始

### 3.1 前置条件

| 依赖 | 说明 |
|---|---|
| Python 3.11+ | **必须是原生 Windows 版 Python**（本机：`D:\python\python.exe`，3.13.9）。⚠️ msys2 的 mingw64 Python 用不了：它的平台标签是 `mingw_x86_64`，装不到 PyPI 的 `win_amd64` 轮子，`pydantic-core` 之类会退化成源码编译并报 `Rust not found`（已实测） |
| Redis | 会话记忆与限流用。本机用社区 Windows 构建：<br>`cd /d/redis/redis-8.10.2/Redis-8.10.2-Windows-x64-msys2 && ./redis-server.exe redis.conf --save 3600 1 --save 300 100 --save 60 10000`<br>⚠️ **别漏掉后面的快照参数**：该 `redis.conf` 里 `appendonly no` 且没有 `save` 行，重启一次就**把登录态（Sa-Token）和会话记忆全清空**，表现是"明明刚登录却提示没有权限" |
| MySQL + wayn_shop | mall 落库用（`mysql -uroot -p -e "CREATE DATABASE wayn_shop"` + 导入 `../db-init/wayn_shop_20230719.sql`） |
| RabbitMQ 4.2.9 | **下单落库、未付款超时关单都靠它**（下单走 outbox → relay → MQ → `waynboot-message` 消费端）。本机装在 `D:\rabbitmq`（zip 解压，无需管理员）：先设 `ERLANG_HOME=D:\rabbitmq\erlang`、`RABBITMQ_BASE=D:\rabbitmq\data`，再跑 `D:\rabbitmq\server42\rabbitmq_server-4.2.9\sbin\rabbitmq-server.bat`<br>⚠️ **版本别乱升**：延迟消息插件最新版只支持 broker **4.2.x**（用 4.3 会报 `supported by the plugin: ["4.2.0-4.2.x"]`）。插件启用一次即可：`rabbitmq-plugins.bat enable rabbitmq_delayed_message_exchange` |
abbitmq`（zip 解压，无需管理员）：<br>`set ERLANG_HOME=D:
abbitmq\erlang` + `set RABBITMQ_BASE=D:
abbitmq\data`，然后跑<br>`D:
abbitmq\server42
abbitmq_server-4.2.9\sbin
abbitmq-server.bat`<br>⚠️ **版本别乱升**：延迟消息插件最新版只支持 broker **4.2.x**（用 4.3 会报 `supported by the plugin: ["4.2.0-4.2.x"]`）。`rabbitmq-plugins.bat enable rabbitmq_delayed_message_exchange` 只需执行一次 |
| waynboot-message-consumer | 消费端，端口 85：`java -jar ../jars/waynboot-message-consumer.jar`。**不起它订单就永远停在"未付款"**（消息发了没人消费） |
| waynboot-mobile-api | Java 侧服务，端口 82：`UPLOAD_DIR=D:/waynshop/webp java -jar ../jars/waynboot-mobile-api.jar`（先在根目录 `mvn package -DskipTests`）。⚠️ **`UPLOAD_DIR` 要带上**：上游默认是 `E:\waynshop\webp`，本机没有 E 盘，不带这个变量商品图片会全部 500（前端显示占位图）。**没有它也能启动 Agent**，只是所有 mall 工具会走降级话术 |
| LLM API Key | DeepSeek（OpenAI 兼容接口）。优先读 `AGENT_LLM_API_KEY`，**没配则回退读系统环境变量 `DEEPSEEK_API_KEY`**（密钥不进仓库）。**都不配也能启动**：`/api/chat` 返回降级话术（`degraded: true`）。可用模型以 `GET {base_url}/models` 为准（当前：`deepseek-flash` / `deepseek-v4-pro`） |

### 3.2 启动

**一键启动（推荐，Windows）**：双击项目根目录的 `start-all.bat` —— 6 个服务各开一个窗口，
环境变量（`UPLOAD_DIR`、`ERLANG_HOME`、`RABBITMQ_BASE`）都带好了，日志写到 `logs/` 下。
停止用 `stop-all.bat`。启动完打开 <http://localhost:8300> 即可。

<details>
<summary>手动逐个启动（macOS/Linux 或想自己控制时）</summary>

```bash
cd waynboot-agent

# 1. 创建虚拟环境并安装依赖（用原生 Windows Python，见 3.1）
D:/python/python.exe -m venv .venv
source .venv/Scripts/activate     # Windows Git Bash
pip install -e ".[dev]"

# 2. 配置（复制样例并按需修改）
cp .env.example .env

# 3. 启动
uvicorn app.main:app --port 8300 --reload
```
</details>

### 3.3 接口一览

| 接口 | 用途 |
|---|---|
| `GET /healthz` | 存活探针（不碰任何外部依赖） |
| `GET /readyz` | 就绪探针：探 mall + Redis，任一不可达返回 503 |
| `POST /api/chat` | 一轮对话，一次性返回 JSON（脚本/调试方便） |
| `POST /api/chat/stream` | 一轮对话，SSE 流式（前端用） |
| `POST /api/chat/confirm` | **确认/取消写操作**（人工确认闸门，见下） |
| `GET /api/sessions/{id}` | 会话概览（消息条数、待处理写入数，直接从 Redis 读） |
| `DELETE /api/sessions/{id}` | 清空会话（幂等） |
| `POST /api/login/code` | 登录代理：转发"发送短信验证码"给 mall |
| `POST /api/login` | 登录代理：拿 mall 签发的 Sa-Token 交给前端 |
| `GET /api/usage` | 当前调用方的 token 用量（按天，身份与限流一致） |
| `ANY /api/mall/{path}` | **商城业务接口的反向代理**（BFF，白名单见 `app/mall/proxy.py`） |
| `GET /` | 商城 H5 页面（单页多视图，`#/xxx` 是页面路由） |
| `GET /chat`、`GET /ui/` | AI 客服聊天页与静态资源 |

### 3.4 写操作的人工确认流程（最重要的一条交互）

Agent 不会自己动用户的订单。任何写操作（申请退款、加入购物车）都走这个闭环：

```
用户："帮我把订单退了"
  ↓
Agent 调 request_refund → 工具只做只读校验 → interrupt() 暂停图
  ↓
HTTP 响应带 pending_action：
  { "action": "request_refund",
    "ask": "确认为订单 2024010112345678 申请退款吗？",
    "summary": "订单 2024010112345678（小米手机 12 x2）申请退款",
    "impact": "订单会进入「申请退款中」，之后不能再取消或确认收货；真正退款由平台审核后处理。",
    "reversible": false }
  ↓  前端弹确认卡片；此时 mall 侧【一个字节都没写】
用户点"确认" → POST /api/chat/confirm {"session_id": "...", "approved": true}
  ↓
工具重跑一遍校验（订单归属、状态可能已变）→ 带幂等键真正执行 → Agent 组织话术
```

**约定**：确认请求必须再带一次 `Authorization`（恢复时工具会从头重跑，凭证不能省）。
连点确认不会重复提交（幂等键 `sha256(session:动作:目标)`，TTL 10 分钟）。

### 3.5 冒烟测试

```bash
# 存活探针：不依赖任何外部服务，永远应返回 200
curl -s http://localhost:8300/healthz

# 就绪探针：会真的去 ping 一次 mall 和 Redis；任一不可达返回 503（联调先看这个）
curl -s http://localhost:8300/readyz

# 一次性对话
curl -s -X POST http://localhost:8300/api/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: <你的 Sa-Token>" \
  -d '{"session_id":"demo","message":"我的订单到哪了？"}'

# 流式对话（-N 关掉 curl 缓冲）
curl -s -N -X POST http://localhost:8300/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","message":"退货政策是什么？"}'
# 事件：token（逐字文本）/ tool（工具调用开始与结束）/ done（本轮结束，含 degraded 标记）

# 看会话状态（注意 session_id 只允许 [A-Za-z0-9_.-]，因为它会拼进 Redis key）
curl -s http://localhost:8300/api/sessions/demo
curl -s -X DELETE http://localhost:8300/api/sessions/demo
```

> ⚠️ Windows 的 Git Bash 控制台是 GBK，直接 `curl -d '{"message":"中文"}'` 会发成非 UTF-8 而报
> `There was an error parsing the body`。用 Python/httpx 发请求，或把请求体写进 UTF-8 文件后 `--data-binary @file`。

### 3.6 打开商城页面（最直观的验证方式）

```bash
# 1) 起 Redis  2) 起 mall（82）  3) 起 Agent
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8300
# 4) 浏览器打开 http://localhost:8300
```

前端是一套**原生 HTML/CSS/JS 的商城 H5**（无 npm、无构建步骤），页面之间用 `#/xxx` 哈希路由切换：

| 页面 | 路由 | 说明 |
|---|---|---|
| 登录 | `#/login` | 手机号 + 验证码（开发环境固定 `1234`），走 Agent 的登录代理 |
| 首页 | `#/home` | 轮播 + 金刚区 + 推荐商品（滚动加载） |
| 分类 | `#/category` | 左一级 / 右二级，点进去是分类商品列表 |
| 搜索 | `#/search` | 热门词 + 联想 + 历史 + 结果（结果由 mall 的 ES 查询提供） |
| 商品详情 | `#/goods/{id}` | 图集 + **规格选择** + 库存 + 加购 |
| 购物车 | `#/cart` | 勾选 / 改数量 / 删除 / 结算 |
| 结算 | `#/checkout` | 地址 + 优惠券 + 备注 → 提交订单（**异步下单 + 轮询结果**，见下） |
| 收银台 | `#/pay/{orderSn}` | 只开放 `payType=99` 测试支付，真实渠道需要商户参数 |
| 订单 | `#/orders`、`#/order/{sn}` | 列表按状态分页 + 详情 + 取消/确认收货/退款/删除 |
| 我的 | `#/profile` | 用户信息 + 订单状态入口 + 地址管理 + 退出登录 |
| 编辑资料 | `#/profile/edit` | 昵称 / 性别 / 头像（预设选一个）+ 改密码 |
| 商品评价 | `#/comments/{goodsId}` | 好评/中评/差评分页；商品详情页有摘要区块，已收货订单可发表评价 |
| 限时秒杀 | `#/seckill`、`#/seckill/{id}` | 活动列表 + 详情；抢购走「先拿令牌 → 提交 → 轮询结果」 |
| **AI 客服** | `/chat` | 商城页右下角浮标、或个人中心「AI 客服对话」进入；流式对话 + 写操作确认卡片。快捷项分两种：信息完整的（我的订单/退货政策/转人工）点了直接发；缺信息的（找商品/算价格）只把话头填进输入框，等你补完再发 |

页面上能做的事（都是真实链路，不是假数据）：

- **验证码登录**：任意合法手机号会自动注册。登录走 Agent 的**登录代理**，
  浏览器不必直连 82 端口（避免跨域）；登录态与客服页共享。
- **流式对话**：逐字输出，工具调用时显示"查询中：my_orders"这类提示。
- **写操作确认卡片**：Agent 要动订单时不会直接做，而是推一张卡片
  （要做什么 / 影响 / 能不能撤销），点「确认执行」才真正调 mall；点「取消」什么都不发生。
- **转人工提示**：`escalate=true` 时页面会插一条提示（前端可据此接人工队列）。
- **会话管理**：新会话 / 清空会话，页脚实时显示 Redis 里的会话状态（消息条数、待确认动作数）。

> ⚠️ **下单是异步的**：`POST /order/submit` 只把消息写进 outbox（本地消息表），
> 由 relay 投给 RabbitMQ、`waynboot-message` 消费端才真正落库；接口立刻返回订单号。
> 所以结算页会轮询 `GET /order/searchResult/{orderSn}`，并且**宽限期内的失败要当成"处理中"**
> ——上游把"还没处理完"和"真的失败了"都返回成同一条失败文案（15 秒内一律按处理中看待）。
> **没有 RabbitMQ 时这一环走不通**（订单不会落库），其余页面不受影响。

想改前端直接读 `app/static/` 即可：`index.html`（商城壳）、`chat.html`（客服页）、
`css/`（样式）、`js/pages/*.js`（一页一个文件）、`js/api.js`（唯一的网络出口）。

#### 前端怎么验证（没有浏览器自动化也能测）

| 手段 | 命令 | 覆盖什么 |
|---|---|---|
| 契约冒烟 | `.venv/Scripts/python.exe scripts/smoke_frontend.py` | 页面**要发的每个请求**（25 项）：静态资源 MIME、首页/分类/搜索/详情/购物车/结算/订单，以及代理白名单与 A15 越权拦截 |
| DOM 渲染检查 | `cd tools/frontend-check && npm install && npm run check` | 15 个页面**在真实 DOM 里渲染一遍**（jsdom）：不抛异常、关键元素出现、导航栏按钮真的绑上了。**这类问题只有真跑 DOM 才会暴露**——踩过一次首页白屏（`Cannot set properties of null`），契约冒烟测不到 |

#### 本地演示数据（可选）

上游只带数据库不带图片，商品也没有规格（SKU 列表是空的）。两个脚本补上：

```bash
# 生成图片（按库里真实引用到的文件名，1185+ 张，约 9.5MB）
.venv/Scripts/python.exe scripts/gen_demo_images.py            # 默认写到 D:/waynshop/webp
# 补 12 个带 SKU 的演示商品（60 个货品），并按 outbox 协议同步 ES
.venv/Scripts/python.exe scripts/seed_demo_goods.py
# 补 4 张演示优惠券（上游 shop_coupon 表是空的，结算页会一直"暂无可用"）
.venv/Scripts/python.exe scripts/seed_demo_coupons.py
# 造 3 个秒杀活动（上游两张秒杀表也是空的；会把活动库存写进 Redis，这一步不能省）
.venv/Scripts/python.exe scripts/seed_demo_seckill.py
.venv/Scripts/python.exe scripts/gen_demo_images.py            # 再跑一次给新商品补图（已存在的会跳过）
```

### 3.7 没有 mall 也能跑端到端：契约替身

真 mall 要 MySQL + Redis + ES + RabbitMQ，本机常常凑不齐。只想验证
"真模型 → 工具 → HTTP → 人工确认 → 写请求"这条链路时，用桩服务就够：

```bash
# 终端 1：假 mall（只监听 127.0.0.1:82，写接口无真实后果，并刻意复刻真 mall 的几个坑）
.venv/Scripts/python.exe scripts/fake_mall.py

# 终端 2：Agent（真模型 + 真 Redis）
uvicorn app.main:app --port 8300

# 终端 3：问它"帮我把订单退了"，看确认卡片，再确认
```

它会打印收到的每一个请求 —— 这就是"Agent 到底发了什么"的直接证据。
**注意：它不能替代对真 mall 的联调**（字段契约来自文档而非运行实例）。

## 4. 目录结构

```
waynboot-agent/
├── README.md                  # 本文件
├── pyproject.toml             # 依赖与工具配置
├── .env.example               # 配置样例（key 不入库）
├── docs/
│   ├── PLAN.md                # 总计划：修复清单 + 阶段路线图 + 进度日志
│   ├── API_CONTRACT.md        # 工具 ↔ mall 接口契约（开发依据，含字段与坑）
│   ├── DECISIONS.md           # 设计决策记录（D1–D17）
│   └── A12-optimistic-lock-proposal.md  # Java 侧商品并发编辑的修复方案书
├── app/
│   ├── main.py                # FastAPI 入口（探针 + 对话 + 确认 + 会话管理 + 登录代理）
│   ├── static/                # 前端（原生 HTML/CSS/JS，无构建）
│   │   ├── index.html         #   商城壳（单页多视图，哈希路由）
│   │   ├── chat.html          #   AI 客服页
│   │   ├── css/               #   base.css（设计变量+通用件）/ app.css / chat.css
│   │   └── js/                #   api.js（唯一网络出口）/ router.js / widgets.js / pages/*.js
│   ├── config.py              # 配置绑定（pydantic-settings，AGENT_ 前缀）
│   ├── schemas.py             # 请求/响应模型
│   ├── observability.py       # 日志 + request id 贯穿
│   ├── graph/                 # LangGraph 编排
│   │   ├── builder.py         #   图装配（agent ⇄ tools + tools_condition）
│   │   ├── nodes.py           #   agent 节点
│   │   ├── runner.py          #   单轮执行 / 恢复 / 降级话术
│   │   ├── streaming.py       #   SSE 事件流（token / tool / done）
│   │   ├── prompts.py         #   系统提示词（能力边界与话术禁区）
│   │   ├── llm.py             #   LLM 构造（DeepSeek，OpenAI 兼容）
│   │   └── state.py           #   图状态（只放对话，凭证不进状态）
│   ├── tools/                 # 工具层
│   │   ├── base.py            #   取 token / 错误归一 / 输出裁剪
│   │   ├── local.py           #   faq（政策问答）、calculator（安全算术）
│   │   ├── faq_data.py        #   FAQ 知识库条目
│   │   ├── goods.py           #   search_goods、goods_detail
│   │   ├── orders.py          #   my_orders、order_detail、logistics（含归属校验）
│   │   ├── coupons.py         #   my_coupons
│   │   └── write.py           #   request_refund、add_to_cart、escalate_to_human
│   ├── mall/                  # mall HTTP 客户端（鉴权透传、R 信封解包、错误归一）
│   │   └── proxy.py           #   BFF 反向代理（浏览器只跟 Agent 说话，白名单校验）
│   └── memory/                # 复用 mall 的 Redis，独立前缀
│       ├── redis_saver.py     #   自研 checkpointer（会话记忆）
│       ├── idempotency.py     #   写操作幂等键
│       └── ratelimit.py       #   限流与日配额
├── scripts/
│   ├── fake_mall.py           # 契约替身（没有真 mall 时做端到端联调）
│   ├── smoke_e2e.py           # 客服链路端到端冒烟（11 项检查，退出码可接 CI）
│   ├── smoke_frontend.py      # 商城前端契约冒烟（25 项：把页面要发的请求打一遍）
│   ├── gen_demo_images.py     # 生成演示商品图片（上游不带图片文件）
│   └── seed_demo_goods.py     # 补带 SKU 的演示商品 + 按 outbox 协议同步 ES
├── tools/
│   └── frontend-check/        # jsdom 页面渲染检查（npm run check，15 个页面）
└── tests/                     # 177 项单测（不依赖真实 LLM / 网络 / mall）
```

## 5. 工具清单（字段契约详见 docs/API_CONTRACT.md）

| 工具 | 类型 | 对应 mall 接口 | 状态 |
|---|---|---|---|
| `faq` | 只读（本地） | 内置政策条目 | ✅ P1 |
| `calculator` | 只读（本地） | AST 白名单求值（不用 eval） | ✅ P1 |
| `search_goods` | 只读 | `GET /category/index` + `GET /category/firstCategoryGoods`（分类扫描 + 本地名称匹配；ES 就绪后切 `GET /search/result`） | ✅ P2 |
| `goods_detail` | 只读 | `GET /goods/detail/{goodsId}` | ✅ P2 |
| `my_orders` | 只读（需登录） | `GET /order/list?showType=` | ✅ P2 |
| `order_detail` | 只读（需登录） | `GET /order/detail/{orderSn}` | ✅ P2 |
| `logistics` | 只读（需登录） | 复用 `GET /order/detail/{orderSn}` 的发货字段 | ✅ P2 |
| `my_coupons` | 只读（需登录） | `GET /coupon/myList` | ✅ P2 |
| `escalate_to_human` | 只读（本地） | 转人工（不调 mall） | ✅ P4 |
| `request_refund` | **写（需确认）** | `POST /order/refund/{orderId}` | ✅ P4 |
| `add_to_cart` | **写（需确认）** | `POST /cart` | ✅ P4 |
| `category_goods` | 只读 | `GET /category/firstCategoryGoods` | ⏳ P5 可选 |
| `cancel_order` / `receive_coupon` | **写（需确认）** | `POST /order/cancel/{id}` / `POST /coupon/receive` | ⏳ P5 可选 |

### 已知限制（诚实清单）

1. **`search_goods` 不是全文搜索**：mall 的 `/search/result` 强依赖 Elasticsearch（本机没有），
   而首页推荐接口 `/home/recommonGoodsList` **只返回固定 10 条**（且路径拼写就是 `recommon`）。
   现在的实现是：按一级分类逐个拉商品（`/category/firstCategoryGoods?pageSize=200`，去重 + 过滤下架）
   再本地按名称匹配——每次搜索约 9 个请求，能覆盖整个商品表。ES 就绪后应切回 `/search/result`。
2. **库存可能是缓存值**：`/goods/detail` 走 Redis 缓存（最长 5 分钟）。
3. **上游越权缺陷**：`/order/detail`、退款、取消等接口原先都不校验订单归属。
   **mall 侧已修复**（A15/A16，加了 `validateOrderOwner` → 5012），Agent 侧仍保留自己的 fail-closed 校验做纵深防御。
   另有 A17（两个状态轮询接口按单号可越权读）待决策，见 docs/PLAN.md。

## 6. 开发工作流

```bash
# 单元测试（不依赖真实 LLM / 网络 / mall / Redis——用 FakeLLM + MockTransport + fakeredis）
.venv/Scripts/python.exe -m pytest -q

# 代码风格
.venv/Scripts/python.exe -m ruff check app tests scripts
.venv/Scripts/python.exe -m ruff format app tests scripts

# 端到端冒烟（需要 Agent 已启动；有真 mall 或 fake_mall 都行）
.venv/Scripts/python.exe scripts/smoke_e2e.py
```

**提交前自检**：`pytest -q` 全绿 + `ruff check` 无报错。

> 测试为什么能脱离外部依赖：LLM 用 `tests/fakes.py` 的 `ScriptedLLM`（只实现 `bind_tools`/`ainvoke`），
> mall 用 `httpx.MockTransport` 注入，Redis 用 `fakeredis`（由 `tests/conftest.py` 的 `fake_redis` 夹具统一替换）。
> 唯一的例外是 `test_redis_saver.py` 里的**真 Redis 集成用例**——它在 Redis 不可达时自动跳过。

## 7. 关键工程保证（可被测试验证）

| 保证 | 靠什么 | 测试 |
|---|---|---|
| LLM 的决策碰不到核心链路 | 所有读写都走 mall 现成接口，Agent 不直连数据库 | 全部工具均为 HTTP 调用 |
| **写操作未经确认绝不执行** | LangGraph `interrupt` + 前置校验在 interrupt 之前 | `test_confirm_api.py`（含"确认前零写入"断言） |
| **连点确认不会重复写** | 幂等键 `sha256(会话:动作:目标)` + SETNX | `test_idempotency.py`、`test_confirm_api.py` |
| 不越权读他人订单 | fail-closed 归属校验（mall 侧这个洞见 A15/A16） | `test_tools_orders.py`、`test_tools_write.py` |
| 循环可控、失败有出口 | `recursion_limit` 封顶 + 降级话术 + `escalate` 标记 | `test_graph.py` |
| 会话记得住、可查可清 | 自研 Redis checkpointer + 会话管理接口 | `test_session_memory.py`、`test_redis_saver.py` |
| **读不到别人的会话** | 线程键由「身份 + session_id」派生（D14） | `test_session_memory.py`、`test_stream_api.py` |
| 长对话不炸上下文 | 滑动窗口 + "更早的已省略"提示（D15） | `test_graph.py` |
| 成本可查 | 每轮日志 + `/api/usage` 按天账本（D16） | `test_usage.py` |
| 公网不裸奔 | 可选 `AGENT_ACCESS_TOKEN` 闸门（探针/登录代理除外） | `test_access_token.py` |

## 8. 部署与上线前清单

本服务是**无状态进程**（会话、幂等、限流、用量状态全在 Redis），可以多副本水平扩；
但同一 `session_id` 的并发请求建议做粘性路由——同一个会话被两个副本同时跑没有意义。

### 8.1 上线前必须做的三件事

| # | 事项 | 为什么 |
|---|---|---|
| 1 | **前面放网关 / 只在内网暴露** | 8300 本身不做用户鉴权（鉴权靠透传 token，见 D2）。公网直接暴露 = 谁都能拿匿名会话刷我们的 LLM 额度 |
| 2 | 至少配一个 `AGENT_ACCESS_TOKEN` | 配了之后 `/api/chat*`、`/api/sessions*`、`/api/usage` 都需要 `X-Agent-Token`（探针与登录代理不受影响，它们有自己的用途）。这只是"不裸奔"的最低成本做法，替代不了网关 |
| 3 | 确认 `AGENT_HISTORY_MAX_MESSAGES`、`AGENT_RATE_LIMIT_*`、`AGENT_SESSION_TTL_SECONDS` 符合业务预期 | 它们直接决定**成本**（每轮发给模型的量、每人每天能问多少次、会话存多久） |

### 8.2 观测

- **每轮日志**带用量与耗时：`本轮用量：llm_calls=2 input_tokens=1851 output_tokens=139 耗时=2300ms`
- **按天账本**：`GET /api/usage?days=7`（按调用方身份，Redis 里 key 形如 `waynboot:agent:usage:{identity}:{yyyymmdd}`，保留 7 天）
- **链路 trace**：LangChain 支持 LangSmith，**代码零改动**，设环境变量即可
  （`LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY=...`；⚠️ 需要 LangSmith 账号，本机未实测）
- **探针**：`/healthz`（不碰依赖）、`/readyz`（探 mall + Redis，任一不可达返回 503）

### 8.3 容器化

`Dockerfile` + `.dockerignore` 已就绪（⚠️ **本机没装 Docker，未实测**）：

```bash
docker build -t waynboot-agent .
docker run --rm -p 8300:8300   -e AGENT_MALL_BASE_URL=http://host.docker.internal:82   -e AGENT_REDIS_URL=redis://host.docker.internal:6379/0   -e AGENT_LLM_API_KEY=sk-xxx   waynboot-agent
```
