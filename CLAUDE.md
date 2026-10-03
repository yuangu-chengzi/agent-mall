# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

waynboot-mall 是一个 Spring Boot 3.5.14 / JDK 17 的开源 H5 商城后端，包含管理端 API、移动端 API、消息消费服务三个可独立部署的应用。后台管理端保留 Spring Security + JWT，移动端已切换为 Sa-Token。

## 常用命令

```bash
# 构建（跳过测试），jar 输出到 ./jars/
mvn clean package -DskipTests

# 运行所有测试
mvn test

# 按领域模块运行测试
mvn test -pl waynboot-domain-trade
mvn test -pl waynboot-domain-inventory

# 运行单个测试类
mvn test -pl waynboot-domain-trade -Dtest=PaymentCallbackSupportTest

# 运行单个测试方法
mvn test -pl waynboot-domain-trade -Dtest=PaymentCallbackSupportTest#markOrderPaid_alreadyPaid_skipsUpdate
```

## 本地启动

**前置中间件**：MySQL 8.0、Redis 3.0+、RabbitMQ 3.0+（需 delayed-message 插件）、Elasticsearch 7.x（需 IK 分词器和拼音插件）

1. 在 `wayn_shop` 数据库中导入项目根目录的 `wayn_shop_*.sql`
2. 编辑 `waynboot-common/src/main/resources/application-dev.yml`（dev/prod 环境配置集中在 common 模块，三个应用共享），填写本地中间件连接信息
3. 分别启动三个 Spring Boot 主类：
   - `AdminApplication` → 端口 81
   - `MobileApplication` → 端口 82
   - `MessageApplication` → 端口 85

**Docker 部署**：
```bash
# 中间件
docker-compose -f docker-compose-es7-rabbitmq-redis-mysql.yml up -d
# 应用
docker-compose up -d
```

## AI 协作准则

本仓库的 Claude 协作规则吸收 `andrej-karpathy-skills` 的四个原则：编码前思考、简洁优先、精准修改、目标驱动验证。处理非平凡任务时，先说明假设、歧义点、影响范围和验证方式；如果需求可能有多种解释，不要静默选择复杂方案，应先收敛边界或提出更简单方案。

默认做最小可维护改动，不为单次需求新增通用框架、过度抽象或未要求的配置能力。每一行 diff 都应能追溯到用户目标；不要顺手重排无关代码、删除既有注释、重构未涉及链路或清理用户已有改动。发现无关坏味道时，先在反馈中说明，除非用户明确要求，否则不要纳入本次修改。

订单、库存、支付、秒杀、MQ、本地消息、认证等核心链路必须转成可验证目标：先读取现有实现和文档，明确不变量，再按模块边界小步修改，最后用对应模块测试、编译或文档 diff 自检闭环。文档更新同样需要对照代码，发现版本、认证模式、模块职责等描述与现状不一致时，应在本次相关文档中一并修正。

## 模块结构

| 模块 | 职责 |
|---|---|
| `waynboot-common` | 通用配置、切面、策略接口、通用模型和基础设施 |
| `waynboot-domain-api` | 跨领域契约：entity / Mapper / Service 接口 / VO / 枚举 |
| `waynboot-domain-trade` | 订单、支付编排、状态机、本地消息、对账 |
| `waynboot-domain-inventory` | 库存冻结、库存流水、Redis 库存快照、库存对账 |
| `waynboot-domain-goods` | 商品、SKU、类目、搜索和 ES 同步 |
| `waynboot-domain-cart` | 购物车读写与选中商品聚合 |
| `waynboot-domain-promotion` | 优惠券、营销位和 Diamond 策略实现 |
| `waynboot-payment-channel` | 微信、支付宝、易支付的支付 / 退款渠道适配 |
| `waynboot-admin-api` | 管理端 REST 控制器 + Spring Security 配置 + Spring `@Scheduled` 治理任务 |
| `waynboot-mobile-api` | H5 移动端 REST 控制器 |
| `waynboot-message` | RabbitMQ 消息消费（core + consumer 子模块） |
| `waynboot-data` | Redis / Elasticsearch 基础设施封装 |
| `waynboot-util` | 常量、枚举、异常、工具类 |

## 核心架构

### 业务领域（按 `waynboot-domain-*` 拆分）

核心业务逻辑通过 ServiceImpl + Support 类组织，而非直接写在 Controller 中：

```
waynboot-domain-trade/src/main/java/com/wayn/domain/trade/support/order/        # 订单提交、取消、生命周期
waynboot-domain-trade/src/main/java/com/wayn/domain/trade/support/admin/order/  # 管理端发货、退款
waynboot-domain-trade/src/main/java/com/wayn/domain/trade/support/payment/      # 支付回调幂等处理
waynboot-domain-trade/src/main/java/com/wayn/domain/trade/outbox/               # 本地消息表（outbox 模式）中继与补偿
waynboot-domain-trade/src/main/java/com/wayn/domain/trade/support/seckill/      # 秒杀异步落单、超时释放、支付确认
waynboot-domain-inventory/src/main/java/com/wayn/domain/inventory/support/      # 库存冻结、Redis 预扣、库存快照
waynboot-domain-goods/src/main/java/com/wayn/domain/goods/support/              # 商品 ES 同步、变更
waynboot-domain-cart/src/main/java/com/wayn/domain/cart/support/                # 购物车读写
waynboot-domain-promotion/src/main/java/com/wayn/domain/promotion/support/      # 优惠券领取
waynboot-domain-promotion/src/main/java/com/wayn/domain/promotion/support/seckill/ # 秒杀活动、短期 token、漏斗过滤
```

### 关键设计模式

**订单提交链**：`OrderSubmitStockReduceStep` 等实现责任链，Redis 预扣库存作为并发门控，MySQL 为最终数据源，`finally` 块保证 Redis 释放。

**支付回调幂等**：`PaymentCallbackSupport` 使用条件更新（`applyExpectedStatus`）+ 二次查询区分"已被其他线程处理"与"真实失败"。`PaymentFlowSaveResult.DUPLICATE_CONFLICT` 通过抛出 `IllegalStateException` 触发 `TransactionTemplate` 回滚。

**本地消息表（outbox）**：`LocalMessageService` 以 `DuplicateKeyException` 实现幂等插入，指数退避重试（最多 5 次），超限后转 `FAILED` 死信。

**秒杀漏斗模型**：秒杀下单先走活动状态、短期 token、用户频控、SKU 限流和 Redis Lua 库存原子扣减，再通过本地消息可靠投递异步落单。订单仍统一写入 `shop_order` / `shop_order_goods`，活动上下文通过 `shop_order_activity_relation` 关联。

**状态机条件更新**：所有订单状态变更统一使用 `applyExpectedStatus(updateWrapper, sourceStatus)` 做乐观并发控制，`updated == 0` 时抛 `BusinessException`。

**Redis Lua 脚本**：`RedisCache` 中的库存预扣（`luaReserveStock`）和释放（`luaReleaseReservedStock`）均为原子 Lua 脚本，返回值语义：`1`=成功/幂等，`-1`=库存不足，`-2`=未初始化，`-3`=参数非法。

### 数据访问

- ORM：MyBatis-Plus，逻辑删除字段 `del_flag`（1=已删除）
- Mapper XML：`classpath*:mapper/**/*Mapper.xml`
- Redis：通过 `RedisCache` 统一封装，避免直接使用 `RedisTemplate`

### 认证

- 管理端（端口 81）：JWT，有效期 120 分钟，header `Authorization`
- 移动端（端口 82）：Sa-Token，有效期 30 天，header `Authorization`
- 鉴权边界：后台继续使用 Spring Security + `@PreAuthorize`，移动端不要重新引入 Spring Security 过滤链，业务代码统一通过 `MobileSecurityUtils` 读取登录用户

### 定时任务

Spring `@Scheduled` 调度，运行在 `AdminApplication` 进程内：

- 入口类：`waynboot-admin-api` → `com.wayn.admin.api.schedule.TradeGovernanceScheduledTask`
- 配置类：`com.wayn.admin.api.schedule.TradeScheduleProperties`，前缀 `wayn.schedule.trade.*`
- 业务侧 relay：`LocalMessageRelayTask`（`waynboot-domain-trade`，每 5 秒一次）
- 失败处理由 Spring 默认 `TaskUtils.LoggingErrorHandler` 兜底，任务方法不需要 try/catch

### 治理接口

后台交易治理 REST 接口位于 `TradeOpsController`，路径前缀 `/ops/trade`，涵盖本地消息查询/重投、库存对账、Redis 库存快照刷新和支付日终对账。

---

# 📚 学习者个人档案（以下为学习者个人内容，与上游项目无关）

> 本文件第一部分（上方）来自上游仓库 wayn111/waynboot-mall；从本行开始是学习者本人档案与学习计划，同步上游代码时可整体忽略或剔除。

## 关于我

- 大二学生，方向：Java + Agent 开发，目标找实习。
- 学过但理解较浅、需要加深的技术：Spring Boot、Redis、RabbitMQ、LangChain、LangGraph。
- 基本没系统学过：MyBatis-Plus（会用但不懂原理）、Spring Security / Sa-Token、Elasticsearch、Maven 多模块工程、RabbitMQ 高级特性（延迟消息/死信）、分布式事务/一致性方案。
- 总体目标：① 一个模块一个模块吃透 waynboot-mall，夯实后端基础；② 吃透后在该项目上新增一个 Agent 模块；③ 用这个项目丰富简历。

## 教学约定（Claude 必须遵守）

- 把我当「略懂的新手」：讲概念先用类比搭框架，再指代码验证，不假设我会，也不从 Hello World 讲起。
- 一次只推进一个模块/主题；讲完用 2~3 个检查问题确认我真的懂了，再进入下一节。我答不上来时降低难度重讲。
- 指代码时尽量用可点击的文件/行号 markdown 链接，方便我在 IDE 里对照。
- 我学过的技术（Redis/RabbitMQ/Spring Boot/Agent 相关）可以结合理论对照着讲深一点；没学过的（ES、Sa-Token、MyBatis-Plus、outbox 等）当新知识系统讲。
- 每个阶段留一个动手小任务（读代码找答案 / 改代码验证），让我真正"吃透"而不是"看懂"。
- 读代码方法论：先骨架（调用链/签名）后细节；Claude 每模块先给「地图 + 1~2 个必须下钻的点」，其余一律当黑盒（三问法：谁调用它 / 吃进吐出什么 / 失败怎样）。学习者维护 `notes/精读笔记.md`（合集：每模块记职责、上下游、关键类、疑问，末章为缺陷审计清账表）；检查问题就是检索练习，答不上来是正常的学习时刻。

## 学习路线与进度（每次推进后 Claude 更新此表）

推荐阅读顺序原则：自底向上（依赖的地基先看）+ 复杂度递增 + 一条业务主线贯穿。

| # | 阶段 | 覆盖模块 | 核心学习点 | 进度 |
|---|---|---|---|---|
| 0 | 整体导览 | 全项目 | 架构分层、一次下单请求全链路走读（ONBOARDING.md 第五章） | ✅ 已完成 (2026-09-10) |
| 1 | 地基热身 | waynboot-util (37) | Maven 多模块、常量/枚举/异常/工具类的组织方式 | ✅ 已完成 (2026-09-10) |
| 2 | 通用与契约层 | waynboot-common (124)、waynboot-domain-api (169) | 通用配置/切面/策略接口/通用模型；MyBatis-Plus 实体与 Mapper；完整数据模型（ER 视角） | ✅ 已完成 (2026-09-13) |
| 3 | 基础设施 | waynboot-data-redis (7)、waynboot-data-elastic (5) | RedisCache 封装 + Lua 原子脚本（理论对照）；ES 初识与商品同步 | ✅ 已完成 (2026-09-17) |
| 4 | 简单业务域 | domain-cart (5)、domain-goods (17)、domain-promotion (18) | 缓存失效策略、聚合查询、优惠券、策略模式（Diamond） | ✅ 已完成 (2026-09-19) |
| 5 | 消息与本地消息表 | waynboot-message-core/consumer (18) | RabbitMQ 交换机/队列/延迟消息、消费模板、outbox 模式 | ✅ 已完成 (2026-09-19) |
| 6 | 核心：订单+支付 | payment-channel (17)、domain-trade (60) | 订单状态机、下单责任链、支付回调幂等、退款 | ✅ 已完成 (2026-09-21) |
| 7 | 库存专题 | domain-inventory (10) | 可售/冻结库存、Redis 预扣、快照对账（trade 阶段穿插亦可） | ✅ 已完成 (2026-09-21) |
| 8 | 入口与治理 | mobile-api (37)、admin-api (50) | Sa-Token vs Spring Security、定时任务、治理接口 /ops/trade | ✅ 已完成 (2026-09-23) |
| 9 | 秒杀专题 | promotion+trade+inventory 联动 | 漏斗模型、token、Lua 原子扣减、延迟关单、异步落单 | ✅ 已完成 (2026-09-23) |
| 10 | 复盘与测试 | 全部 | 设计模式总结（责任链/策略/模板）、测试规范、写文档 | ✅ 已完成 (2026-09-25；findings 已转为 Part A 修复) |
| 11 | Agent 模块 | `waynboot-agent/` | LangChain + LangGraph 客服 Agent：图编排、工具层、Redis 记忆、SSE、写操作人工确认 | 🔄 开发完成 (2026-09-25)，**精读中**：① 图与状态 ✅ ② 工具层 ✅ ③ 会话记忆 ✅ ④ 人工确认 ✅ ⑤ 幂等与限流 ✅ ⑥ 测试方法论 ⬅ 进行中 |
| 12 | 完整前端 | `waynboot-agent/app/static/` | 商城 H5 全流程页面（登录/首页/分类/搜索/详情/购物车/下单/订单/个人中心 + 评价/秒杀/资料编辑 + 客服入口），原生单页无构建 | ✅ 已完成 (2026-10-03)：18 个页面模块 + BFF 代理；**真实闭环已跑通**（加购→下单落库→测试支付→退款→AI 查单）；验证：契约冒烟 30/30、jsdom 全过、Java 233 项 + Python 178 项 |

辅助资料：根目录 `ONBOARDING.md`（作者写的新手指南，15 章）、`docs/`、`db-init/`（SQL）。
注意：README.md 中 Spring Boot "3.1.4" 已过时，实际为 3.5.14 + JDK 17（见 pom.xml）。

## 阶段 11 产出：`waynboot-agent/`（学习者新增的 Agent 模块）

**这是我（学习者）在 mall 之上新增的独立服务**，不是上游仓库的内容；动手开发由 Claude 代做，我要做的是**精读 + 改 + 讲**。

| 项 | 内容 |
|---|---|
| 技术栈 | Python 3.13 + FastAPI + LangGraph 1.x + LangChain + Redis |
| 与 mall 的关系 | 通过 HTTP 调用 `waynboot-mobile-api`（端口 82）的**现成接口**，零改动；用户 Sa-Token 透传 |
| 运行 | **一键**：双击项目根目录 `start-all.bat`（起 Redis/ES/RabbitMQ/mall/消费端/Agent 六个服务，参数都带好了）；单独起 Agent 用 `cd waynboot-agent` → `.venv/Scripts/python.exe -m uvicorn app.main:app --port 8300`（必须用**原生 Windows Python**，msys2 的 mingw Python 装不了依赖） |
| 页面 | 浏览器打开 `http://localhost:8300` → **商城 H5**（登录/首页/分类/搜索/详情/购物车/结算/支付/订单/我的/评价/秒杀/编辑资料，`#/xxx` 哈希路由）；右下角浮标进 `http://localhost:8300/chat` → 流式对话 + 写操作确认卡片。前端全在 `app/static/`（原生 HTML/CSS/JS，无 npm 无构建），浏览器只跟 Agent 说话（`/api/mall/**` 是 BFF 代理，见 DECISIONS D17） |
| 测试 | `.venv/Scripts/python.exe -m pytest -q`（178 项，不依赖真实 LLM/网络/Redis）；Java 侧 `mvn -o test`（230 项） |
| 前端验证 | ① 契约冒烟 `scripts/smoke_frontend.py`（25 项，打一遍页面要发的请求）② DOM 渲染检查 `cd tools/frontend-check && npm install && npm run check`（15 个页面在 jsdom 里真渲染一遍）③ 客服链路 `scripts/smoke_e2e.py`（11 项） |
| 演示数据 | `scripts/gen_demo_images.py`（生成商品图片）+ `scripts/seed_demo_goods.py`（补 12 个带 SKU 的商品并同步 ES）+ `scripts/seed_demo_coupons.py`（补演示优惠券）——上游只带 SQL 不带图片、商品也没规格 |
| 端到端 | `scripts/fake_mall.py`（契约替身）+ `scripts/smoke_e2e.py`（11 项检查） |
| 文档 | `docs/PLAN.md`（进度与验证缺口）、`docs/API_CONTRACT.md`（字段与坑）、`docs/DECISIONS.md`（D1–D13） |
| 工具 | 11 个：faq / calculator / search_goods / goods_detail / my_orders / order_detail / logistics / my_coupons / request_refund / add_to_cart / escalate_to_human |
| 必读的三个"设计点" | ① 决策与执行分离（LLM 不碰核心链路）② 写操作走 `interrupt` 人工确认 + 幂等键 ③ 自研 Redis checkpointer（官方实现要 RedisJSON/RediSearch，本机 Redis 没有） |

**状态**：**对真 mall 的联调已完成**（2026-09-25）——真模型 + 真 mall 跑通读与写（退款落库 202）、端到端脚本 11/11。
MySQL 已建库导入（`wayn_shop`，41 表 / 234 商品），Redis 用社区 Windows 构建，**ES 7.17.29 已装 + **IK/pinyin 插件已自编译并生效**（`D:/elasticsearch/es/elasticsearch-7.17.29/bin/elasticsearch.bat`，9200，mapping 用上游的 `ik_max_word`/`pinyin`；插件源码在 `C:/Users/20361/AppData/Local/Temp/plugins-src`，重装需删掉 IK 里的 log4j-api 以免 jar hell），三个应用都用 `java -jar jars/*.jar` 启动。

**已完成（2026-10-02）**：① **RabbitMQ 已装，下单闭环打通**——Erlang 27 + RabbitMQ **4.2.9** + 延迟消息插件，装在 `D:\rabbitmq`；⚠️ **broker 版本要配插件**（插件最新版只支持 4.2.x，4.3 用不了），且必须起 `waynboot-message-consumer.jar`（消费端，85）。真机闭环已验证：加购 → 下单落库 → 测试支付 → 申请退款，订单表/状态日志/outbox 三方一致。启动命令见 `waynboot-agent/README.md` §3.1。
**已知未完成项**：① 转人工只到「标记 + 日志」，没有工单表 ② 管理端口令未知（`/shop/goods/syncEs` 这类管理端接口无法调用，除非用户提供）③ ~~A19 待决策~~ **已修复**（2026-10-03）：`/pay/prepay` 补归属校验，真机验证 B 付 A 的单被 5012 拦下④ ~~`shop_coupon` 表为空~~ 已用 `seed_demo_coupons.py` 补 4 张券。
**启动 mall 必须带 `UPLOAD_DIR=D:/waynshop/webp`**（默认值指向不存在的 E 盘，不带则商品图片全部 500）。
**已完成的生产化加固（2026-09-29）**：A17/A18 越权修复、长对话窗口、用量观测（`/api/usage`）、可选访问闸门、Dockerfile；详见 `docs/DECISIONS.md` D14–D16 与 PLAN 的 P7。
⚠️ `application-dev.yml` 里目前是本机真实 MySQL 口令，**提交前要改回占位值**。

### Java 侧追加修复（A15/A16，2026-09-25）

上游的移动端订单接口**只按订单号/订单 id 查单、不校验归属**，属于 IDOR：任意登录用户可读（甚至操作）他人订单。
已修：新增 `OrderValidationSupport.validateOrderOwner(order, userId)`（抛 5012），`userId` 从控制器经 `IMobileOrderService`
传进 domain-trade，覆盖 **详情 / 退款 / 取消 / 确认收货 / 删除** 五个入口；顺带修掉"取消订单在非未付款状态下静默返回 200"。
改动**只碰 mobile 调用链**（管理端走 `AdminOrderQuerySupport` / `AdminOrderRefundSupport`，未受影响）；
验证：强制全量重编 + `mvn -o test` → 224 项 0 失败。细节见 `waynboot-agent/docs/PLAN.md` 的 A15/A16 行。
