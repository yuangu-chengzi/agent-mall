package com.wayn.domain.trade.outbox;

import com.alibaba.fastjson.JSON;
import com.wayn.domain.api.goods.entity.Goods;
import com.wayn.domain.api.goods.service.IGoodsService;
import com.wayn.domain.api.outbox.entity.LocalMessage;
import com.wayn.domain.api.outbox.service.LocalMessageTopics;
import com.wayn.domain.goods.support.GoodsElasticSyncSupport;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.io.IOException;

/**
 * 商品变更本地消息处理器：把商品最新状态同步到 ES。
 * <p>
 * relay 扫描到没有 MQ 路由（exchange/routingKey 为空）的 GOODS_CHANGED 消息时调用本处理器。
 * 处理天然幂等（按商品 ID upsert / 删除文档），因此本地消息重试不会产生重复副作用；
 * 每次处理都重新读取商品当前状态，多次连续变更会被"合并"为同步到最新状态。
 */
@Slf4j
@Service
@AllArgsConstructor
public class GoodsChangedLocalMessageHandler implements LocalMessageHandler {

    private static final String GOODS_ID_FIELD = "goodsId";

    private final IGoodsService goodsService;
    private final GoodsElasticSyncSupport goodsElasticSyncSupport;

    /**
     * 是否支持商品变更主题。
     *
     * @param topic 消息主题
     * @return true=支持
     */
    @Override
    public boolean supports(String topic) {
        return LocalMessageTopics.GOODS_CHANGED.equals(topic);
    }

    /**
     * 处理商品变更消息：商品存在则 upsert 文档，已删除则删除文档。
     *
     * @param message 本地消息
     */
    @Override
    public void handle(LocalMessage message) {
        Long goodsId = parseGoodsId(message.getPayload());
        Goods goods = goodsService.getById(goodsId);
        try {
            if (goods == null || Boolean.TRUE.equals(goods.getDelFlag())) {
                // 商品已删除：按文档 ID 删除（ES 删除不存在的文档是幂等的）
                goodsElasticSyncSupport.deleteGoodsFromEs(goodsId);
                return;
            }
            goodsElasticSyncSupport.syncGoods2Es(goods);
        } catch (IOException e) {
            // 包装为运行时异常：relay 记录失败并按指数退避重试，超限转 FAILED 死信
            throw new IllegalStateException("商品变更同步 ES 失败, goodsId=" + goodsId, e);
        }
    }

    /**
     * 解析消息体中的商品 ID。
     *
     * @param payload JSON 消息体
     * @return 商品 ID
     */
    private Long parseGoodsId(String payload) {
        Long goodsId = JSON.parseObject(payload).getLong(GOODS_ID_FIELD);
        if (goodsId == null) {
            throw new IllegalArgumentException("商品变更消息缺少 goodsId, payload=" + payload);
        }
        return goodsId;
    }
}
