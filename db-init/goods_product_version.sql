-- 商品 SKU 表：乐观锁版本号（A12）
-- 背景：管理端编辑商品时，"库存差量更新"只防住了毫秒级竞态，挡不住"打开编辑页 → 保存"之间的陈旧表单覆盖
--       （例：页面看到 10、改成 20，期间用户买走 3，保存后库存被写成 20 而不是 17 → 超卖风险）。
-- 方案：编辑请求携带页面读到的 version，保存时由 MyBatis-Plus 乐观锁插件追加 WHERE version = ? 条件；
--       更新 0 行即视为"编辑期间已被修改"，拒绝保存并提示刷新。
-- 说明：下单冻结/确认/释放、退款回补等库存变更走手写 SQL 条件更新，不参与版本号。
ALTER TABLE `shop_goods_product`
    ADD COLUMN `version` int NOT NULL DEFAULT 0 COMMENT '乐观锁版本号（商品编辑用，下单扣减不参与）' AFTER `locked_stock`;
