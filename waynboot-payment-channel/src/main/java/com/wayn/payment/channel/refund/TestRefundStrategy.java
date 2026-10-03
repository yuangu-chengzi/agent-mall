package com.wayn.payment.channel.refund;

import com.wayn.domain.api.trade.enums.PayTypeEnum;
import com.wayn.common.design.strategy.refund.strategy.RefundInterface;
import com.wayn.domain.api.trade.request.OrderRefundReqVO;
import com.wayn.common.model.response.OrderRefundResVO;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.retry.annotation.Backoff;
import org.springframework.retry.annotation.Retryable;
import org.springframework.stereotype.Component;

/**
 * 测试退款策略。
 * 不调用任何退款渠道，直接返回成功，用于在无真实商户号时跑通本地退款闭环。
 * 由 {@code wayn.pay.test.enabled} 开关控制，默认关闭——生产环境必须保持关闭。
 */
@Slf4j
@Component
@AllArgsConstructor
@ConditionalOnProperty(prefix = "wayn.pay.test", name = "enabled", havingValue = "true")
public class TestRefundStrategy implements RefundInterface {

    @Retryable(maxAttempts = 3, backoff = @Backoff(delay = 2000L, multiplier = 1.5))
    @Override
    public OrderRefundResVO refund(OrderRefundReqVO reqVo) {
        return new OrderRefundResVO();
    }

    @Override
    public Integer getType() {
        return PayTypeEnum.TEST.getType();
    }
}
