package com.wayn.domain.api.outbox.service;

/**
 * 本地消息主题常量。
 * 统一定义业务写入和 relay/handler 识别使用的主题，避免硬编码分散在订单链路中。
 */
public final class LocalMessageTopics {

    public static final String BIZ_TYPE_ORDER = "ORDER";

    public static final String BIZ_TYPE_GOODS = "GOODS";

    public static final String ORDER_SUBMIT = "ORDER_SUBMIT";

    /**
     * 商品变更（新增/编辑）主题：无 MQ 路由，由本地处理器同步 ES。
     */
    public static final String GOODS_CHANGED = "GOODS_CHANGED";

    public static final String ORDER_UNPAID_DELAY = "ORDER_UNPAID_DELAY";

    public static final String ORDER_PAID_POST_ACTION = "ORDER_PAID_POST_ACTION";

    public static final String SECKILL_ORDER_SUBMIT = "SECKILL_ORDER_SUBMIT";

    /**
     * 工具类禁止实例化。
     */
    private LocalMessageTopics() {
    }
}
