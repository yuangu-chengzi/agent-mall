package com.wayn.payment.channel.pay;

import cn.hutool.core.lang.id.NanoId;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.wayn.domain.api.trade.entity.Order;
import com.wayn.domain.api.trade.entity.OrderGoods;
import com.wayn.domain.api.goods.service.IGoodsService;
import com.wayn.domain.api.trade.service.IOrderGoodsService;
import com.wayn.domain.api.trade.service.IOrderService;
import com.wayn.domain.api.trade.enums.PayTypeEnum;
import com.wayn.common.design.strategy.pay.strategy.PayTypeInterface;
import com.wayn.domain.api.trade.request.OrderPayReqVO;
import com.wayn.domain.api.trade.response.OrderPayResVO;
import com.wayn.util.enums.OrderStatusEnum;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.Date;
import java.util.List;

/**
 * 测试支付策略。
 * 不调用任何支付渠道，直接把订单标记为已支付，用于在无真实商户号时跑通本地支付闭环。
 * 由 {@code wayn.pay.test.enabled} 开关控制，默认关闭——生产环境必须保持关闭，否则可被用于刷单。
 */
@Slf4j
@Component
@AllArgsConstructor
@ConditionalOnProperty(prefix = "wayn.pay.test", name = "enabled", havingValue = "true")
public class TestPayStrategy implements PayTypeInterface {
    private IOrderService orderService;
    private IOrderGoodsService iOrderGoodsService;
    private IGoodsService iGoodsService;

    @Override
    public OrderPayResVO pay(OrderPayReqVO reqVo) {
        Order order = orderService.getOne(new QueryWrapper<Order>().eq("order_sn", reqVo.getOrderSn()));
        order.setPayId(NanoId.randomNanoId());
        order.setPayTime(LocalDateTime.now());
        order.setOrderStatus(OrderStatusEnum.STATUS_PAY.getStatus());
        order.setUpdateTime(new Date());
        orderService.updateById(order);
        updateVirtualSales(order.getId());
        return new OrderPayResVO();
    }

    private void updateVirtualSales(Long orderId) {
        try {
            List<OrderGoods> orderGoods = iOrderGoodsService.list(Wrappers.lambdaQuery(OrderGoods.class)
                    .eq(OrderGoods::getOrderId, orderId));
            for (OrderGoods orderGood : orderGoods) {
                Long goodsId = orderGood.getGoodsId();
                Integer number = orderGood.getNumber();
                iGoodsService.updateVirtualSales(goodsId, number);
            }
        } catch (Exception e) {
            log.error(e.getMessage(), e);
        }
    }

    @Override
    public Integer getType() {
        return PayTypeEnum.TEST.getType();
    }
}
