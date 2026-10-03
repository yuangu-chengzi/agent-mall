package com.wayn.domain.trade.support.order;

import com.wayn.domain.api.common.MybatisPlusTableInfoTestHelper;

import com.wayn.domain.api.trade.entity.Order;
import com.wayn.domain.api.trade.mapper.OrderMapper;
import com.wayn.domain.api.trade.service.IOrderGoodsService;
import com.wayn.domain.api.trade.service.OrderStatusLogService;
import com.wayn.util.enums.OrderStatusEnum;
import com.wayn.util.enums.ReturnCodeEnum;
import com.wayn.util.exception.BusinessException;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderLifecycleSupportTest {

    /** 订单归属人；所有用例都以此身份操作自己的订单，越权用例特意传别的 id。 */
    private static final Long USER_ID = 7L;
    private static final Long OTHER_USER_ID = 8L;

    @BeforeAll
    static void initTableInfo() {
        MybatisPlusTableInfoTestHelper.init(Order.class);
    }

    @Mock
    private OrderMapper orderMapper;
    @Mock
    private IOrderGoodsService orderGoodsService;
    @Mock
    private OrderCancellationSupport orderCancellationSupport;
    @Mock
    private OrderStatusLogService orderStatusLogService;

    @Test
    void refundThrowsWhenConditionalUpdateAffectsNoRows() {
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(1L, "refund-order", OrderStatusEnum.STATUS_PAY);
        when(orderMapper.selectById(1L)).thenReturn(order);
        when(orderMapper.update(any(), any())).thenReturn(0);

        BusinessException exception = assertThrows(BusinessException.class, () -> support.refund(1L, USER_ID));

        assertEquals(ReturnCodeEnum.ERROR.getCode(), exception.getCode());
    }

    @Test
    void confirmThrowsWhenConditionalUpdateAffectsNoRows() {
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(2L, "confirm-order", OrderStatusEnum.STATUS_SHIP);
        when(orderMapper.selectById(2L)).thenReturn(order);
        when(orderMapper.update(any(), any())).thenReturn(0);

        BusinessException exception = assertThrows(BusinessException.class, () -> support.confirm(2L, USER_ID));

        assertEquals(ReturnCodeEnum.ERROR.getCode(), exception.getCode());
    }

    @Test
    void deleteThrowsWhenConditionalDeleteAffectsNoRows() {
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(3L, "delete-order", OrderStatusEnum.STATUS_CONFIRM);
        when(orderMapper.selectById(3L)).thenReturn(order);
        when(orderMapper.delete(any())).thenReturn(0);

        BusinessException exception = assertThrows(BusinessException.class, () -> support.delete(3L, USER_ID));

        assertEquals(ReturnCodeEnum.ERROR.getCode(), exception.getCode());
    }

    @Test
    void cancelDelegatesToCancellationSupport() {
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(4L, "cancel-order", OrderStatusEnum.STATUS_CREATE);
        when(orderMapper.selectById(4L)).thenReturn(order);
        when(orderCancellationSupport.cancel("cancel-order", OrderStatusEnum.STATUS_CANCEL)).thenReturn(true);

        boolean cancelled = support.cancel(4L, USER_ID);

        assertEquals(true, cancelled);
        verify(orderCancellationSupport).cancel("cancel-order", OrderStatusEnum.STATUS_CANCEL);
    }

    @Test
    void cancelReturnsFalseWhenOrderIsNotCancellable() {
        // 取消支撑服务在状态不允许时返回 false（不抛异常）；调用方必须如实返回这个结果，
        // 否则接口会"谎报成功"——这正是修复前的缺陷。
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(9L, "not-cancellable-order", OrderStatusEnum.STATUS_SHIP);
        when(orderMapper.selectById(9L)).thenReturn(order);
        when(orderCancellationSupport.cancel("not-cancellable-order", OrderStatusEnum.STATUS_CANCEL)).thenReturn(false);

        assertEquals(false, support.cancel(9L, USER_ID));
    }

    @Test
    void refundRecordsStatusLogWhenConditionalUpdateSucceeds() {
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(5L, "refund-success-order", OrderStatusEnum.STATUS_PAY);
        when(orderMapper.selectById(5L)).thenReturn(order);
        when(orderMapper.update(any(), any())).thenReturn(1);

        support.refund(5L, USER_ID);

        verify(orderStatusLogService).recordSuccess(any());
    }

    @Test
    void confirmRecordsStatusLogWhenConditionalUpdateSucceeds() {
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(6L, "confirm-success-order", OrderStatusEnum.STATUS_SHIP);
        when(orderMapper.selectById(6L)).thenReturn(order);
        when(orderMapper.update(any(), any())).thenReturn(1);

        support.confirm(6L, USER_ID);

        verify(orderStatusLogService).recordSuccess(any());
    }

    /**
     * 越权防护：操作别人的订单必须被拒（返回 5012「订单用户和当前登陆用户不一致」），且不能产生任何副作用。
     */
    @Test
    void refundRejectsOrderOwnedByAnotherUser() {
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(7L, "other-user-order", OrderStatusEnum.STATUS_PAY);
        when(orderMapper.selectById(7L)).thenReturn(order);

        BusinessException exception = assertThrows(BusinessException.class, () -> support.refund(7L, OTHER_USER_ID));

        assertEquals(ReturnCodeEnum.ORDER_USER_NOT_SAME_ERROR.getCode(), exception.getCode());
        verify(orderMapper, never()).update(any(), any());
        verify(orderStatusLogService, never()).recordSuccess(any());
    }

    @Test
    void cancelRejectsOrderOwnedByAnotherUser() {
        OrderLifecycleSupport support = buildSupport();
        Order order = buildOrder(8L, "other-user-order-2", OrderStatusEnum.STATUS_CREATE);
        when(orderMapper.selectById(8L)).thenReturn(order);

        BusinessException exception = assertThrows(BusinessException.class, () -> support.cancel(8L, OTHER_USER_ID));

        assertEquals(ReturnCodeEnum.ORDER_USER_NOT_SAME_ERROR.getCode(), exception.getCode());
        verify(orderCancellationSupport, never()).cancel(any(), any());
    }

    private OrderLifecycleSupport buildSupport() {
        return new OrderLifecycleSupport(orderMapper, orderGoodsService,
                new OrderValidationSupport(), orderCancellationSupport, new OrderStateTransitionSupport(),
                orderStatusLogService);
    }

    private Order buildOrder(Long id, String orderSn, OrderStatusEnum statusEnum) {
        Order order = new Order();
        order.setId(id);
        order.setOrderSn(orderSn);
        order.setUserId(USER_ID);
        order.setOrderStatus(statusEnum.getStatus());
        return order;
    }
}
