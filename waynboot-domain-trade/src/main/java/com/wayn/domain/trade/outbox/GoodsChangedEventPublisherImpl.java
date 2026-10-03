package com.wayn.domain.trade.outbox;

import com.alibaba.fastjson.JSON;
import com.wayn.domain.api.outbox.service.GoodsChangedEventPublisher;
import com.wayn.domain.api.outbox.service.LocalMessageTopics;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.Map;

/**
 * 商品变更事件发布器实现（基于本地消息表 / outbox）。
 * <p>
 * 在调用方事务内写入 local_message；relay 扫描时发现该消息没有 exchange/routingKey，
 * 会路由给本进程的 {@link GoodsChangedLocalMessageHandler} 执行 ES 同步，失败按指数退避重试。
 */
@Slf4j
@Service
@AllArgsConstructor
public class GoodsChangedEventPublisherImpl implements GoodsChangedEventPublisher {

    private static final String GOODS_CHANGED_KEY_PREFIX = "GOODS_CHANGED:";
    private static final String GOODS_ID_FIELD = "goodsId";

    private final LocalMessageService localMessageService;

    /**
     * 发布商品变更事件。
     *
     * @param goodsId       商品 ID
     * @param changeVersion 变更版本（更新时间的毫秒值）
     */
    @Override
    public void publishGoodsChanged(Long goodsId, long changeVersion) {
        if (goodsId == null) {
            log.warn("跳过商品变更消息保存，goodsId 为空");
            return;
        }
        // messageKey 携带变更版本：商品每次变更都必须是独立消息，
        // 否则第二次变更会被唯一键判为"重复"而被静默丢弃（旧消息可能已 SENT，不会再次投递）。
        localMessageService.saveMessage(LocalMessageCreateCommand.builder()
                .messageKey(GOODS_CHANGED_KEY_PREFIX + goodsId + ":" + changeVersion)
                .topic(LocalMessageTopics.GOODS_CHANGED)
                .bizType(LocalMessageTopics.BIZ_TYPE_GOODS)
                .bizId(String.valueOf(goodsId))
                .payload(JSON.toJSONString(Map.of(GOODS_ID_FIELD, goodsId)))
                .build());
    }
}
