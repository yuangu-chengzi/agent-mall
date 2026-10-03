package com.wayn.domain.api.outbox.service;

/**
 * 商品变更事件发布器（契约）。
 * <p>
 * 由上层模块（waynboot-domain-trade）基于本地消息表实现；商品写路径在业务事务内调用本接口，
 * 使"商品数据变更"与"待同步 ES 的本地消息"同事务提交，从而保证搜索索引最终一致（失败可重试）。
 * 采用"契约在 domain-api、实现在 domain-trade"的方式，避免领域实现模块之间产生循环依赖。
 */
public interface GoodsChangedEventPublisher {

    /**
     * 发布商品变更事件。
     *
     * @param goodsId       商品 ID
     * @param changeVersion 变更版本（建议取商品更新时间的毫秒值；同一商品多次变更必须是不同版本，
     *                      否则会被本地消息表的唯一键判为重复而丢消息）
     */
    void publishGoodsChanged(Long goodsId, long changeVersion);
}
