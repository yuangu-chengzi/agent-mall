# 工具 ↔ mall 接口契约

> 本文是 Agent 工具层与 waynboot-mall（mobile-api，端口 82）之间的**接口契约**，Python 工具层按本表实现。
> 字段均已回源码核对（附 file:line），**未经运行实例实测**（本机 mall 起不来：缺 Redis、MySQL 口令不符、无 ES）。
> 核对结论与旧版有冲突的，以本文为准；已订正的旧错误见文末「订正记录」。

---

## 1. 通用约定

### 1.1 响应信封与三条硬规则

所有接口返回统一信封（`com.wayn.util.util.R`，`waynboot-util/.../util/R.java:20-29`）：

```json
{ "code": 200, "msg": "操作成功", "data": { } }
```

- `code == 200` 为成功，其余为业务错误（`msg` 可直接作为话术素材）。
- **硬规则 1：HTTP 状态码几乎恒为 200。** 业务错误、未登录都由 `@RestControllerAdvice` 返回 `R` 对象，
  没有 `@ResponseStatus` → 客户端**必须看 `code`，不能看 HTTP status**。
  （`GlobalExceptionHandler.java:23-58`、`MobileSaTokenExceptionHandler.java:26-30`）
- **硬规则 2：`null` 字段被省略。** `spring.jackson.default-property-inclusion: non_null`
  （`waynboot-mobile-api/src/main/resources/application.yml:21`）→ 值为 null 的 key **根本不出现**在 JSON 里
  （不是 `"key": null`）。工具层一律用 `.get("key")` 取值，**不要**用 `data["key"] is None` 判断。
- **硬规则 3：分页参数来自 `BaseController.getPage()`**（`waynboot-common/.../BaseController.java:60-79`）：
  `pageNum`（默认 1）、`pageSize`（默认 10）、`sortName`、`sortOrder`。
  ⚠️ `sortName` 会与 SQL 里已有的 `order by` 叠加，**工具层一律不传排序参数**。

### 1.2 鉴权

| 场景 | 请求头 |
|---|---|
| 需登录接口 | `Authorization: <Sa-Token>`（裸 token 或 `Bearer <token>` 均可，`MobileAuthorizationHeaderFilter.java:42-52` 会裁掉前缀） |
| 匿名接口 | 不带 |

**透传规则**：Agent 从自己的请求头取 `Authorization`，调用 mall 时原样携带（决策 D2）。
未登录时 mall 返回 `{"code":401,"msg":"没有权限，请联系管理员授权"}`（HTTP 200）。

### 1.3 匿名白名单（来自 `MobileSaTokenConfiguration.java:38-59`）

```
/home/**   /category/**   /goods/detail/**   /comment/**   /search/**   /diamond/**
/coupon/list   /cart/goodsCount   /seckill/**   /login   /registry
```

⚠️ 注意 **`/coupon/myList` 不在白名单**（要登录），而 `/coupon/list`（可领券）在。

### 1.4 常用错误码（`ReturnCodeEnum`）

| code | 含义 | 客服话术建议 |
|---|---|---|
| 200 | 成功 | — |
| 401 | 未登录（`UNAUTHORIZED`） | "请先登录后再查询" |
| 500 | 兜底异常（含 ES 不可用） | 通用失败话术 + 转人工 |
| 5001 | 下单失败 | 通用失败话术 + 转人工 |
| 5002 | 订单不存在 | "没有找到这笔订单，请核对订单号" |
| 5003 | 订单不能退款 | "当前订单状态不支持退款" |
| 5006 | 订单不能支付 | "订单当前状态无法支付" |
| 5007 | 订单不能取消 | "订单当前状态无法取消" |

---

## 2. 工具清单与接口映射

### 2.1 只读工具（Agent 可自主调用）

| 工具 | 用途 | mall 接口 | 参数 | 鉴权 | 状态 |
|---|---|---|---|---|---|
| `faq` | 政策/流程答疑 | **本地**（内置 FAQ） | `question` | — | ✅ P1 已实现 |
| `calculator` | 算术（价格/优惠/运费） | **本地**（AST 白名单求值） | `expression` | — | ✅ P1 已实现 |
| `search_goods` | 找商品（按名称匹配） | `GET /home/recommGoodsList` | `keyword`、`pageSize` | 匿名 | ✅ P2 |
| `goods_detail` | 商品详情（价格/规格/库存） | `GET /goods/detail/{goodsId}` | `goodsId` | 匿名 | ✅ P2 |
| `my_orders` | 我的订单列表 | `GET /order/list?showType=` | `showType` | **需登录** | ✅ P2 |
| `order_detail` | 订单详情（含物流） | `GET /order/detail/{orderSn}` | `orderSn` | **需登录** | ✅ P2 |
| `logistics` | 物流查询 | 复用 `order_detail` 的发货字段 | `orderSn` | **需登录** | ✅ P2 |
| `my_coupons` | 我的优惠券 | `GET /coupon/myList` | 分页 | **需登录** | ✅ P2 |
| `category_goods` | 按分类逛商品 | `GET /category/firstCategoryGoods` | `cateId`、分页 | 匿名 | ⏳ 待做（P5 可选） |
| `coupon_list` | 可领优惠券 | `GET /coupon/list` | 分页 | 匿名 | ⏳ 待做（P5 可选） |

> **ES 未就绪的应对**：`/search/result` **强依赖 Elasticsearch 且无 DB 兜底**（`SearchApplicationService.java:96`），
> 本机没有 ES → `search_goods` 用 `/home/recommGoodsList` 拉一批商品在**本地按 `name` 子串匹配**。
> 该接口固定按 `create_time desc` 排序，且**没有 `is_on_sale` 过滤**（下架商品也在列表里），
> 所以工具输出必须带"仅为推荐列表内匹配，库存/在售状态需查商品详情"的说明。

### 2.2 写操作工具（必须人确认后执行）

| 工具 | 用途 | mall 接口 | 参数 | 幂等键 | 状态 |
|---|---|---|---|---|---|
| `request_refund` | 申请退款 | `POST /order/refund/{orderId}`（**无请求体**） | `orderId`（订单主键 id） | `sha256(session + "refund" + orderId)` | ✅ P4 |
| `add_to_cart` | 加入购物车 | `POST /cart`（body：`goodsId`/`productId`/`number`） | 三字段 | `sha256(session + "cart" + goodsId + productId)` | ✅ P4 |
| `escalate_to_human` | 转人工 | **不调 mall**（本地记录 + 事件） | `reason` | 不需要（只读语义） | ✅ P4 |
| `cancel_order` | 取消订单 | `POST /order/cancel/{orderId}` | `orderId` | `sha256(session + "cancel" + orderId)` | ⏳ P5 可选 |
| `receive_coupon` | 领券 | `POST /coupon/receive` | `couponId`(Integer) | `sha256(session + "coupon" + couponId)` | ⏳ P5 可选 |

> **幂等实现**：Agent 侧维护"已执行动作"记录（Redis，key = 幂等键，TTL 10 分钟）；命中则直接返回上次结果，
> 不重复调用 mall。mall 侧自身的幂等（购物车锁/券限领/订单状态机）作为第二道防线。

---

## 3. 逐接口字段契约（P2 实现依据）

### 3.1 `GET /home/recommonGoodsList`（⚠️ 路径拼写以真机为准）

> **真机联调订正（2026-09-25，两个坑）**：
> ① 路径是 **`/home/recommonGoodsList`**——作者把 recommend 拼成了 `recommon`
> （`HomeController.java:61`）。按"正确"拼写 `/home/recommGoodsList` 请求会被当成静态资源，
> 抛 `NoResourceFoundException` → 全局兜底返回 **`{"code":500,"msg":"操作失败"}`**（不是 404！）。
> ② 该方法**不接受任何参数**（无 `getPage()`），`pageSize` 传了也无效，**永远只返回 10 条推荐位商品**
> → 用它当"搜索"等于只搜 10 个商品。
>
> **因此 `search_goods` 改走分类接口**（见 §3.6）：`/category/index` + 逐个
> `/category/firstCategoryGoods?cateId=X&pageSize=200`（后者实测支持 pageSize，一个大类能一次拿 164 个）。

- Controller：`HomeController.java:61-71`（`@RequestMapping("home")`，匿名）
- **响应**：`R<List<RecommonGoodsItemResVO>>` → `data` 是**裸数组**（无 total/records 包装），固定 10 条。
- 排序固定 `create_time desc, id desc`；**无 `is_on_sale` 过滤**（`GoodsMapper.xml:61-82`）。

`RecommonGoodsItemResVO`（`waynboot-common/.../model/response/RecommonGoodsItemResVO.java:17-26`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Long | 商品 ID（→ 传 `goods_detail`） |
| `name` | String | 名称（本地匹配用） |
| `brief` | String | 简介 |
| `picUrl` | String | 列表图 |
| `counterPrice` / `retailPrice` | BigDecimal | 专柜价 / **零售价（展示用这个）** |
| `isNew` / `isHot` | Boolean | 新品 / 人气 |
| `actualSales` / `virtualSales` | Integer | 实际销量 / 虚拟销量 |

⚠️ **无库存字段** → "有没有货"必须查 `goods_detail`。

### 3.2 `GET /goods/detail/{goodsId}`

- Controller：`GoodsController.java:33-39`（`@RequestMapping("goods")`，匿名，**不继承 BaseController**，无分页）
- 响应：`R<GoodsDetailResponseVO>`（`GoodsDetailResponseVO.java:24-36`）→ 四层结构：

| 字段 | 类型 | 展开 |
|---|---|---|
| `info` | `Goods`（裸实体） | `id`、`goodsSn`、`name`、`categoryId`、`brandId`、`gallery`(String[])、`keywords`、`brief`、`isOnSale`(Boolean，1=上架)、`sort`、`picUrl`、`shareUrl`、`isNew`、`isHot`、`unit`、`counterPrice`、`retailPrice`、`actualSales`、`virtualSales`、**`detail`（富文本 HTML，可能极大）**、`delFlag`、`createTime`、`updateTime` |
| `productList` | `List<GoodsProduct>` | SKU：`id`（加购用 `productId`）、`goodsId`、`specifications`(String[])、`price`（**SKU 价**）、`defaultSelected`、**`number`（可售库存）**、`lockedStock`（冻结）、`version`（乐观锁）、`url` |
| `specificationList` | `List<SpecificationVO>` | `name`（规格名）、`valueList`(List<GoodsSpecification>：`id`/`goodsId`/`specification`/`value`/`picUrl`) |
| `attributes` | `List<GoodsAttribute>` | `id`/`goodsId`/`attribute`（参数名）/`value`（参数值） |

**库存语义**（`GoodsProductMapper.xml:40-61` 实证）：`number` 已是扣掉冻结的可售量，**回答"还有货吗"直接用 `number`**，
不要再减 `lockedStock`；总库存 = `number + lockedStock`。

⚠️ 该接口走 Redis 缓存 `GOODS_DETAIL_CACHE`（`GoodsDetailServiceImpl.java:51-82`）→ 库存可能是缓存值（最长 5 分钟）。
⚠️ `goodsId` 不存在时 `info` 被省略、三个列表为空数组，**`code` 仍为 200** → 工具层要判空并给出"商品不存在"。

**工具层裁剪**（上下文经济）：`info.detail`（HTML 富文本）与 `gallery` 不入返回值，`brief` 截断。

### 3.3 `GET /order/list`

- Controller：`OrderController.java:55`（`@RequestMapping("order")`，**需登录**）
- 参数：`showType`（默认 0：0 全部 / 1 待付款 / 2 待发货 / 3 待收货 / 4 待评价；**其他值 = 不筛状态**）+ 分页。
- **响应：`R<OrderListResVO>` —— 注意双层 `data`**：外层信封的 `data` 里又有一个 `data` 数组。

| 路径 | 类型 | 说明 |
|---|---|---|
| `data.data` | `List<OrderListDataResVO>` | 订单数组（空结果 `[]`） |
| `data.pages` | Long | 总页数 |
| `data.page` | Long | 当前页（**叫 `page`，不是 `current`**） |

`OrderListDataResVO`（`OrderListDataResVO.java:24-46`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Long | 订单 ID（→ 退款/取消接口用） |
| `orderSn` | String | 订单号（→ `order_detail` 用） |
| `actualPrice` | BigDecimal | 实付金额 |
| `orderStatusText` | String | **状态文案（列表里没有数字 `orderStatus`）** |
| `handleOption` | `OrderHandleOption` | 布尔组：`cancel`/`delete`/`pay`/`comment`/`confirm`/`refund`/`rebuy`/`aftersale` |
| `goodsList` | `List<OrderGoodsVO>` | 见下（**详情里叫 `orderGoodsVOList`，键名不同**） |

`OrderGoodsVO`：`id`/`orderId`/`goodsId`/`goodsName`/`goodsSn`/`number`/`price`/`specifications`(String[])/`picUrl`/`comment`。

⚠️ 列表项**没有下单时间字段**。

### 3.4 `GET /order/detail/{orderSn}`

- Controller：`OrderController.java:41`（path variable `orderSn`，**需登录**）
- 响应：`R<OrderDetailVO>`（`OrderDetailVO.java:24-176`，**无 `@JsonProperty`，JSON 名 = 字段名**）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` / `userId` / `orderSn` | Long/Long/String | `userId` 可用于所有权核对 |
| `orderStatus` | Short | **数字状态**：101 未付款 / 102 已取消 / 103 已取消(系统) / 201 已付款 / 202 取消退款中 / 203 已退款 / 301 已发货 / 401 已收货 / 402 已收货(系统) |
| `orderStatusText` | String | 状态文案（`OrderUtil.java:21-65`） |
| `aftersaleStatus` | Short | 售后状态（0 可申请/1 已申请/2 审核通过/3 退款成功/4 审核拒绝/5 已取消，**仅注释，无枚举**） |
| `consignee` / `mobile` / `address` / `message` | String | 收货信息 |
| `goodsPrice` / `freightPrice` / `couponPrice` / `orderPrice` / `actualPrice` | BigDecimal | 金额 |
| `payType` / `payTypeText` | Integer/String | 支付方式文案来自 `OrderUtil.payTypeText()`：1 微信 / 2 支付宝 / 3 TEST / 其他→空串 |
| `payTime` / `shipTime` / `refundTime` / `confirmTime` / `orderEndTime` / `createTime` | 时间 | 格式 `yyyy-MM-dd HH:mm:ss`，**为 null 则 key 缺失** |
| `shipSn` / `shipChannel` | String | 快递单号 / 快递公司（未发货则 key 缺失） |
| `refundAmount` / `refundType` / `refundContent` | BigDecimal/Integer/String | 退款信息 |
| `orderGoodsVOList` | `List<OrderGoodsVO>` | 商品明细 |

⚠️ **`OrderDetailVO` 里没有 `refundStatus`**（旧版本文档写错了）：退款状态要看 `aftersaleStatus` + `refundAmount/refundTime`。
（`refundStatus` 只存在于实体 `Order.java:133`，不对外暴露。）

⚠️ **`/order/detail/{orderSn}` 没有归属校验**（`OrderQuerySupport.java:165-166` 只按 `orderSn` 查）
→ 任意登录用户凭单号可读他人订单。**这是上游缺陷（见 §5 A15）**，Agent 侧已加 fail-closed 所有权前置校验。

### 3.5 `GET /coupon/myList`（需登录）

- Controller：`CouponController.java:63`；参数只有分页（**没有 `useStatus` 过滤**，已用/已过期券也会返回，无 ORDER BY）
- 响应：`R<Page<MemberCouponResVO>>`（MP 的 `Page` 直接序列化）→ 键只有 5 个：
  `records`（数组）、`total`、`size`、`current`（**叫 `current`**）、`pages`

`MemberCouponResVO`（`MemberCouponResVO.java:14-58`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Long | 用户券记录 ID（非券 ID） |
| `title` | String | 券名 |
| `couponId` | Integer | `shop_coupon.id` |
| `discount` | Integer | 优惠金额（**单位未确认：元/分**，源码与库注释都没写） |
| `min` | Integer | 使用门槛（**JSON 键就叫 `min`**） |
| `useStatus` | Integer | 0 未使用 / 1 已使用 / 2 已过期 |
| `usedTime` / `createTime` / `expireTime` | Date | `yyyy-MM-dd HH:mm:ss`，null 则 key 缺失 |

⚠️ 与 `/coupon/list` 的 `ShopCouponResVO` 字段完全不同（后者有 `status`/`type`/`num`/`receiveStatus`）。

### 3.6 `GET /category/**`（匿名）——`search_goods` 的实际依赖

- `GET /category/index`：无参数；响应 `{currentCategory, categoryList[], subCategoryList[]}`
  （`VanTreeSelectVO` = `id`/`text`/`icon`）；结果缓存 300s。实测一级分类 8 个（居家/餐厨/配件/服装/洗护/婴童/杂货/饮食）。
- `GET /category/firstCategoryGoods`：参数 **`cateId`**（默认 0，**不是 `categoryId`**）+ `pageNum`/`pageSize`；
  响应 `{goods: List<Goods 裸实体>, category}`。
  **实测**：`cateId=1005000&pageSize=200` → 返回 **164 个商品**（支持 pageSize，SQL 自带 `is_on_sale=1`）。
  ⚠️ 语义是"查 `cateId` 的**二级子分类**下的商品"，不含 `cateId` 自身挂的商品。

> **`search_goods` 的实现**：`/category/index` 拿一级分类 → 逐个 `firstCategoryGoods?pageSize=200` →
> 按商品 id 去重（下架过滤）→ 本地名称子串匹配。代价是每次搜索约 9 个请求，换来能覆盖整个商品表。

### 3.7 `GET /search/result`（匿名，**强依赖 ES**）

- Controller：`SearchController.java:56-70`；参数 `keyword`（主条件）、`isNew`/`isHot`/`isPrice`/`isSales`/`filterNew`/`filterHot`/`orderBy`、分页；
  `categoryId`/`brandId` **未使用**。响应 `R<List<SearchGoodsItemResVO>>`（裸数组，20 字段，**无库存字段**）。
- ES 不可用时抛 `IOException` → `{"code":500,"msg":"操作失败"}`（HTTP 200）。**本机无 ES，暂不用。**

---

### 3.8 写操作接口（P4 实现依据）

三个共同点，**每一个都踩过坑**：

1. **业务失败也是 HTTP 200**，必须看 `code`；
2. **写接口的 `data` 是 `null`**（`R.success()` 无参重载）→ 不能用 `data == true` 判成功；
3. **都不校验订单归属**（见 §5 A15/A16）→ Agent 侧必须先自己确认"这笔单是当前用户的"。

#### `POST /order/refund/{orderId}`（申请退款）

- Controller：`OrderRefundController.java:34`，路径变量 `orderId`（**订单主键 id，不是订单号**）
- **请求体：无**（`OrderRefundReqVO` 只属于后台管理端接口，含 `refundMoney`/`refundReason`，移动端传不了也不接受）
- 前置：订单存在（否则 5002）；`handleOption.refund == true` 即 `order_status == 201`（已付款未发货），否则 **5003**；
  状态机 201 → 202
- 副作用：`order_status=202`、`refund_status=1` + 写一条 `shop_order_status_log`。**不调用支付渠道、不写退款流水、不回补库存、无事务注解**
- ⚠️ **钱不会退**：这只是"提交申请"，真正退款要管理员在后台执行 → **话术不能说"已退款/已到账"，也不能承诺可撤销**（移动端没有撤销入口）
- 工具侧判定：**不信 mall 的返回码，先用 `/order/list` 的 `handleOption.refund` 做前置判断**（见 A16）

#### `POST /cart`（加入购物车）

- Controller：`CartController.java:56`，body = `CartAddReqVO`：`goodsId`(Long) / `productId`(Long) / `number`(Integer)，
  **都没有校验注解**，由服务端手工校验（缺任一或 number≤0 → **4002**）
- 校验：商品存在；**必须上架**（否则 5302）；货品存在且属于该商品；`number` > 可售库存 → **5303**
  （只对比 `number`，不减 `locked_stock`）
- ⚠️ **重复加购是累加**（`CartWriteSupport.java:74-78`）：已有 5 件再加 5 件 = 10 件，且按累加后的值校验库存
  → 所谓"幂等"在 mall 侧并不成立，Agent 侧的幂等键是**唯一**的防重复手段
- 用户维度锁 `waynboot:cart_lock:{userId}`，TTL 30 秒；抢锁失败 → 500 "购物车操作频繁"

#### `POST /order/cancel/{orderId}`（取消订单，P5 可选）

- 仅 `101`（未付款）可取消（101 → 102），会释放冻结库存、回退优惠券、写状态日志
- ⚠️ **重大契约陷阱**：非 101 时**不抛异常、也不改任何数据，但接口返回 `code=200`**
  （`OrderController.java:126` 丢弃了 `canTransition` 的返回值）→ **绝不能只看 code=200 就告诉用户"已取消"**

#### `POST /coupon/receive`（领券，P5 可选）

- body = `{couponId: Integer}`（`@NotNull`，缺失 → code **5000**）
- 限领：**一人一张**（同 `(userId, couponId)` 已存在 → 500 "优惠券已经领过了"）、券须上架未过期、防超发
- 返回的 `data` 是**真实 boolean**（与 cart/refund 不同）

---

## 4. 待验证清单（V1–V7 结论）

| # | 项 | 结论 |
|---|---|---|
| V1 | `/category/**` 参数与结构 | ✅ 已核对（注意参数名 `cateId`） |
| V2 | `/search/result` 参数与返回 | ✅ 已核对（强依赖 ES，暂不使用） |
| V3 | `/order/list` 结构 | ✅ 已核对（**双层 data**；列表项无数字状态、无时间） |
| V4 | `/coupon/myList` 字段 | ✅ 已核对（`records/current/pages`；`min`/`discount`/`useStatus`） |
| V5 | `POST /cart` 请求体与校验 | ✅ 已核对（见 §3.8） |
| V6 | `POST /order/refund/{orderId}` 请求体与前置状态 | ✅ 已核对（**无请求体**；仅 201 可退；见 §3.8） |
| V7 | SSE 长连接下 Sa-Token 行为 | ⏳ P3 联调实测 |

**仍未实测的项（必须标注）**：以上结论全部来自源码/字节码核对，**没有一次真实 HTTP 调用验证**——
本机 mall 起不来（无 Redis、MySQL root 口令与配置不符、无 ES/RabbitMQ）。工具层单测用**按 VO 定义构造的
fixture** 覆盖；真实联调留待环境就绪（见 PLAN §2 联调前置清单）。

---

## 5. 待修的上游缺陷

### A15（已修复 2026-09-25）：`/order/detail/{orderSn}` 缺归属校验（IDOR）

- 证据：`OrderQuerySupport.getOrderDetailByOrderSn` 原先只按 `orderSn` 查，不校验归属。
- 影响：任意已登录用户凭订单号即可读取他人订单（含收货人、手机号、地址）。
- **修复**：新增 `OrderValidationSupport.validateOrderOwner(order, userId)`（抛 5012「订单用户和当前登陆用户不一致」），
  `userId` 由移动端控制器经 `IMobileOrderService` 传入；**只改 mobile 调用链**（已核对：这些方法没有别的调用方，
  管理端走 `AdminOrderQuerySupport` / `AdminOrderRefundSupport`）。
- **同类入口一并修复**：退款、取消、确认收货、删除（A16）；其中取消还顺带修掉"非未付款状态静默返回 200"的问题。
- Agent 侧仍保留自己的 fail-closed 所有权校验（纵深防御，且不假设 mall 一定修过）。

### A19（**待修**，2026-10-02 前端联调时发现）：`POST /pay/prepay` 不校验订单归属

- 证据：`PayController.prepay` 只收 `OrderPayReqVO{orderSn, payType}`，**不传 userId**；
  `PaymentPrepareSupport.prepay` 只做 `ensurePayable(order)`（查 `OrderHandleOption::isPay`，即"状态可支付"），
  **没有** `validateOrderOwner`。同一条链路上的 `order/detail`、`refund`、`cancel`… 都在 A15/A16 加过了，唯独支付漏了。
- 真机复现（2026-10-02，两个账号）：

  | 动作 | 用户 B 对用户 A 的订单 | 结果 |
  |---|---|---|
  | `GET /order/detail/{A的orderSn}` | 被拒 | `code=5012 订单用户和当前登陆用户不一致`（A15 挡住了） |
  | `POST /pay/prepay {A的orderSn, payType:99}` | **成功** | `code=200`，DB 里该单 `order_status` 101→201、写入新 `payId`/`payTime` |

- 影响：`wayn.pay.test.enabled=true` 的环境下，任何登录用户**可以把他人未付款订单直接刷成已付款**；
  生产关掉测试渠道后降级为"可篡改他人订单的支付方式并发起支付请求"（仍属越权）。
- 推荐修法：与 A15 完全一致——控制器取 `MobileSecurityUtils.getUserId()` → 传进 `prepay` →
  在 `ensurePayable` **之前**调 `orderValidationSupport.validateOrderOwner(order, userId)`；
  3 处改动 + 1 个回归测试（"B 支付 A 的订单 → 5012"）。
- 状态：**只验证未改代码**，等用户决策。

---

## 6. 商城前端用到的接口（P8）

前端不直连 mall：浏览器只请求 Agent 的 `/api/mall/**`，由 `app/mall/proxy.py` 原样转发
（**不解信封、不改状态码**，所以下面这些"坑"在前端一样成立）。逐页对应关系与验收见 `PLAN.md` §4.3。

| 页面 | 接口 | 前端的注意点 |
|---|---|---|
| 首页 | `/home/index`、`/home/recommonGoodsList` | 推荐列表**不返回总页数** → 用"本页条数 < pageSize"判断到底 |
| 分类 | `/category/index`、`/category/content?id=`、`/category/firstCategoryGoods`、`/category/secondCategoryGoods` | 参数是 `cateId`；分类商品同样**没有分页信息** |
| 搜索 | `/search/result`、`/search/sugguest`、`/search/hotKeywords`、`/searchHistory/**` | 中文关键词必须 URL 编码（`api.js` 统一做） |
| 详情 | `/goods/detail/{id}` | SKU 靠 `productList[i].specifications` 与 `specificationList` **按下标对齐**；不存在的商品返回空对象（没有 `info`），要兜住 |
| 购物车 | `/cart/list`、`POST /cart`、`POST /cart/addDefaultGoodsProduct`、`PUT /cart`、`/cart/changeNum/{id}/{n}`、`DELETE /cart/{id}`、`/cart/goodsCount`、`POST /cart/getCheckedGoods` | 无规格商品走 `addDefaultGoodsProduct`；写接口 `data` 为 null 但 `code=200` |
| 结算 | `/address/list`、`POST /order/submit`、`/order/searchResult/{sn}` | **下单是异步的**（outbox → MQ → 消费端），必须轮询；"处理中"与"失败"上游返回同一条文案 → 前端用 15 秒宽限期区分 |
| 支付 | `/pay/prepay` | 只有 `payType=99`（测试渠道）在本地可用，其余要商户参数 |
| 订单 | `/order/list`、`/order/detail/{sn}`、`/order/statusCount`、`/order/cancel/{id}`、`/order/confirm/{id}`、`/order/delete/{id}`、`/order/refund/{id}` | 列表是**双层 data**（`{data, pages, page}`）；列表返回 `handleOption`（按钮由后端状态机决定），**详情接口没有** → 详情页按 `OrderUtil.build` 的规则前端推导 |
| 我的 | `/user/info`、`/logout` | — |

---

## 订正记录（相对旧版本文档）

| 旧描述 | 实际（已订正） |
|---|---|
| `OrderDetailVO` 含 `refundStatus` | **没有该字段**，退款看 `aftersaleStatus` + `refundAmount/refundTime` |
| 102/103 = "用户取消/系统取消" | `orderStatusText` 实际为 `"已取消"` / `"已取消(系统)"` |
| `/home/recommGoodsList` 支持 `keyword` | **无 `keyword` 参数**，传了被忽略；返回裸数组、无库存字段 |
| `/category/firstCategoryGoods` 参数 `categoryId` | 实际参数名 **`cateId`** |
| 未说明 | **null 字段被 JSON 省略**（`non_null`）；**业务错误也是 HTTP 200** |
