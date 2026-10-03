# A12 方案书：商品库存编辑的"陈旧表单"丢失更新（乐观锁 + 版本号）

> 状态：**待决策**（涉及 DDL 变更与接口契约变化，未擅自实施）。
> 相关代码：`GoodsMutationSupport.adjustExistingProductStock`、`shop_goods_product` 表、管理端商品编辑接口。

## 1. 问题（缺陷，非取舍）

管理端编辑商品时，`number`（可售库存）通过"差量更新"写库：`delta = 提交值 - 保存时从库里读到的值`，再执行 `number = number + delta`。

**它只保护"重新读库 → 执行 UPDATE"之间毫秒级的并发扣减**，对下面这个**秒级窗口**无效：

```
T0  运营打开编辑页，页面显示可售库存 = 10
T1  运营把库存改成 20（意图：+10）
T2  （页面不刷新）用户下单买走 3 件 → 数据库 number = 7
T3  运营点保存：后端重新读库 existing = 7 → delta = 20 - 7 = +13
T4  写库：number = 7 + 13 = 20
```

- 运营的真实意图是 `7 + 10 = 17`，实际写成 **20** —— 那 3 笔真实销售被"归还"到可售池（**超卖风险**）。
- 根因：**delta 的基准用了"保存时读到的值"，而正确基准应是"操作者看到的值"**；且系统无法判断"操作者看到的页面是否已过期"。

（注：`GoodsMutationSupport` 里现有的 `afterReduce < lockedStock` 校验与 `reduceStock` 的 SQL 守卫是另一层保护，与本问题无关。）

## 2. 方案 A（推荐）：乐观锁 + 版本号

### 2.1 DDL

```sql
ALTER TABLE shop_goods_product
    ADD COLUMN version INT NOT NULL DEFAULT 0 COMMENT '乐观锁版本号';
```

> 需同步补充到 `db-init/` 的增量 SQL（例如新增 `db-init/goods_product_version.sql`），并在 `db-init/wayn_shop_20230719.sql` 的表结构中体现。

### 2.2 实体与配置

- `GoodsProduct` 增加字段：`@Version private Integer version;`
- `MybatisPlusConfig` 启用乐观锁插件（与分页插件并列）：
  `interceptor.addInnerInterceptor(new OptimisticLockerInnerInterceptor());`

### 2.3 接口契约（关键）

管理端商品编辑请求中，**每个已有 SKU 必须回传打开页面时的版本号**（`version`）：

- 后端保存时用 `@Version` 自动追加 `WHERE id = ? AND version = ?`；
- **更新 0 行 → 说明该 SKU 在编辑期间被下单/取消修改过** → 返回明确业务错误：
  `"商品库存已被下单修改，请刷新后重试"`（错误码建议新增 `GOODS_STOCK_CHANGED_ERROR`）；
- 前端收到该错误后**刷新页面并让运营重新确认库存**。

> 兼容性：老前端不传 version 时，`@Version` 不生效（退化为现状行为）。**建议分两步上线**：
> 第一步后端支持（不传 version 保持旧行为）；第二步前端改造 + 强制校验。

### 2.4 影响面与回归测试点

| 影响面 | 说明 |
|---|---|
| 库存扣减链路 | `freezeStock/confirmFrozenStock/releaseFrozenStock/addStock/reduceStock` 都是**手写 SQL 条件更新**，不经过 MP 的 `updateById`，**不受 `@Version` 影响**（需逐一确认，避免误伤） |
| 商品保存链路 | `updateBatchById`（走 MP）→ 会带上 version 条件 → 需为已有 SKU 回填 version |
| 前端 | 需要回传 version（第二步） |
| 测试 | ① 并发下单期间保存商品 → 应报"请刷新"；② 无并发时保存 → 正常；③ 不传 version → 旧行为 |

### 2.5 备选方案对比

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. 乐观锁 version**（推荐） | 语义清晰、通用、可复用到其他编辑场景 | 需要 DDL + 前端配合 |
| B. 库存改为**增量语义**接口（"增加/减少 N 件"） | 语义无歧义、无需 version、贴合"补货"业务 | 管理端 UI/接口都要改；"设置成 20"这类需求无法直接表达 |
| C. 保存前二次确认弹窗（前端比对） | 改动小 | 治标：并发窗口仍在，只是变窄；无法覆盖多标签页等场景 |

**建议**：A 为主，B 可作为长期方向（库存的本质是事件流，见 `shop_inventory_flow`）。另建议为管理端手动调库存**补写库存流水**（当前只有 `update_time`，无法审计"谁把库存改成了 20"）。

## 3. 决策待办

- [ ] 是否采纳方案 A（乐观锁）？
- [ ] 若采纳：DDL 由谁执行（本地自测 / 生产变更单）？
- [ ] 前端（管理端）是否同步改造回传 version？
- [ ] 是否同步补"管理端调库存写流水"（审计）？

---

## 4. 实施记录（2026-09-25，已采纳方案 A）

| # | 改动 | 文件 |
|---|---|---|
| 1 | DDL：`shop_goods_product` 增加 `version int NOT NULL DEFAULT 0` | `db-init/goods_product_version.sql`（增量脚本）、`db-init/wayn_shop_20230719.sql`（全量结构同步） |
| 2 | 实体乐观锁字段 | `GoodsProduct`（`@Version private Integer version;`） |
| 3 | MyBatis-Plus 乐观锁插件（两个进程各一份配置） | `waynboot-mobile-api/.../MybatisPlusConfig`、`waynboot-admin-api/.../MybatisPlusConfig` |
| 4 | 新错误码 | `ReturnCodeEnum.GOODS_STOCK_CHANGED_ERROR(5304, "商品信息已被修改（可能来自并发下单），请刷新后重试")` |
| 5 | 请求 VO 增加 `version`（承载"页面读到的版本号"） | `GoodsProductVO` |
| 6 | 保存链路分流：**带 version** → `updateById` 乐观锁逐条更新，更新 0 行即抛业务异常回滚；**不带** → 保持原批量更新（兼容旧前端） | `GoodsMutationSupport.syncProducts` / 新增 `updateWithOptimisticLock` |
| 7 | 回归测试（冲突拒绝 + 带版本走乐观锁） | `GoodsMutationSupportTest`（新增 2 例，模块 4/4 通过） |

**验证**：`mvn -o test` 全量编译 + 单测通过；`GoodsMutationSupportTest` 4/4。

**未完成（需前端配合 → 第二步）**：管理端商品编辑页需回传每个 SKU 的 `version`。
在此之前修复处于"**基础设施就绪但未生效**"状态（旧前端行为不变，风险不增加）。

**边界核对（重要）**：库存扣减/释放链路（`freezeStock`/`confirmFrozenStock`/`releaseFrozenStock`/`addStock`/`reduceStock`）都是
`GoodsProductMapper.xml` 里的**手写 SQL 条件更新**，不经过 MP 乐观锁插件，因此 **不受 `@Version` 影响**（已逐一核对）。

**执行 DDL 的方式**（二选一）：
```bash
# 已有数据库：执行增量脚本
mysql -uroot -p wayn_shop < db-init/goods_product_version.sql
# 全新安装：直接导入 db-init/wayn_shop_20230719.sql（已含该列）
```
