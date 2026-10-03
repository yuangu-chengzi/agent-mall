package com.wayn.domain.trade.support.payment;

import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.wayn.common.design.strategy.pay.context.PayTypeContext;
import com.wayn.common.design.strategy.pay.strategy.PayTypeInterface;
import com.wayn.domain.api.common.MybatisPlusTableInfoTestHelper;
import com.wayn.domain.api.trade.entity.Order;
import com.wayn.domain.api.trade.mapper.OrderMapper;
import com.wayn.domain.api.trade.request.OrderPayReqVO;
import com.wayn.domain.api.trade.response.OrderPayResVO;
import com.wayn.domain.api.trade.service.IOrderGoodsService;
import com.wayn.domain.trade.support.order.OrderValidationSupport;
import com.wayn.util.enums.OrderStatusEnum;
import com.wayn.util.enums.ReturnCodeEnum;
import com.wayn.util.exception.BusinessException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.math.BigDecimal;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 预支付入口的归属校验回归测试（A19）。
 * <p>
 * 修复前的真实问题：`POST /pay/prepay` 只按订单号查单、只校验"能不能支付"，
 * **完全不校验订单是不是本人的**。真机复现过——B 读 A 的订单会被 5012 拦住，
 * 但 B 用 A 的订单号发起支付却成功了，订单被刷成已付款。
 * <p>
 * 所以这里守两条：**别人的订单必须抛 5012，且连支付渠道都不能碰**；本人的订单照常走。
 */
@ExtendWith(MockitoExtension.class)
class PaymentPrepareSupportTest {

    private static final Long OWNER_ID = 93L;
    private static final Long OTHER_USER_ID = 94L;
    private static final String ORDER_SN = "1790928945000003";

    @Mock
    private OrderMapper orderMapper;
    @Mock
    private IOrderGoodsService orderGoodsService;
    @Mock
    private PayTypeContext payTypeContext;
    @Mock
    private PayTypeInterface payType;

    private PaymentPrepareSupport support;

    @BeforeAll
    static void initTableInfo() {
        MybatisPlusTableInfoTestHelper.init(Order.class);
    }

    @BeforeEach
    void setUp() {
        support = new PaymentPrepareSupport(orderMapper, orderGoodsService, payTypeContext, new OrderValidationSupport());
    }

    @AfterEach
    void clearRequestContext() {
        RequestContextHolder.resetRequestAttributes();
    }

    @Test
    void prepay_rejectsOtherUsersOrder_andNeverTouchesPaymentChannel() {
        when(orderMapper.selectOne(any())).thenReturn(unpaidOrderOf(OWNER_ID));

        BusinessException ex = assertThrows(
                BusinessException.class, () -> support.prepay(payRequest(), OTHER_USER_ID));

        assertEquals(ReturnCodeEnum.ORDER_USER_NOT_SAME_ERROR.getCode(), ex.getCode());
        // 关键：不查商品、不写 payType、不取支付策略——越权请求不该产生任何副作用
        verify(orderGoodsService, never()).list(any(Wrapper.class));
        verify(orderMapper, never()).update(isNull(), any());
        verify(payTypeContext, never()).getInstance(any());
    }

    @Test
    void prepay_rejectsMissingOrder() {
        when(orderMapper.selectOne(any())).thenReturn(null);

        BusinessException ex = assertThrows(
                BusinessException.class, () -> support.prepay(payRequest(), OWNER_ID));

        assertEquals(ReturnCodeEnum.ORDER_NOT_EXISTS_ERROR.getCode(), ex.getCode());
        verify(payTypeContext, never()).getInstance(any());
    }

    @Test
    void prepay_allowsOwnOrder() {
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(new MockHttpServletRequest()));
        when(orderMapper.selectOne(any())).thenReturn(unpaidOrderOf(OWNER_ID));
        when(orderGoodsService.list(any(Wrapper.class))).thenReturn(List.of());
        when(orderMapper.update(isNull(), any())).thenReturn(1);
        when(payTypeContext.getInstance(99)).thenReturn(payType);
        when(payType.pay(any())).thenReturn(new OrderPayResVO());

        OrderPayResVO result = support.prepay(payRequest(), OWNER_ID);

        assertNotNull(result);
        verify(payType).pay(any());
    }

    private Order unpaidOrderOf(Long userId) {
        Order order = new Order();
        order.setId(900004L);
        order.setOrderSn(ORDER_SN);
        order.setUserId(userId);
        order.setOrderStatus(OrderStatusEnum.STATUS_CREATE.getStatus());
        order.setActualPrice(new BigDecimal("899.00"));
        return order;
    }

    private OrderPayReqVO payRequest() {
        OrderPayReqVO reqVO = new OrderPayReqVO();
        reqVO.setOrderSn(ORDER_SN);
        reqVO.setPayType(99);
        return reqVO;
    }
}
