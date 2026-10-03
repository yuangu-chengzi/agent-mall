package com.wayn.domain.trade.support.order;

import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.wayn.domain.api.trade.entity.Order;
import com.wayn.domain.api.trade.entity.OrderGoods;
import com.wayn.domain.api.trade.mapper.OrderMapper;
import com.wayn.domain.api.trade.service.IOrderGoodsService;
import com.wayn.domain.api.common.MybatisPlusTableInfoTestHelper;
import com.wayn.domain.api.trade.response.OrderDetailVO;
import com.wayn.domain.api.trade.response.OrderListDataResVO;
import com.wayn.domain.api.trade.response.OrderListResVO;
import com.wayn.data.redis.manager.RedisCache;
import com.wayn.util.enums.OrderStatusEnum;
import com.wayn.util.enums.ReturnCodeEnum;
import com.wayn.util.exception.BusinessException;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OrderQuerySupportTest {

    @BeforeAll
    static void initTableInfo() {
        MybatisPlusTableInfoTestHelper.init(Order.class, OrderGoods.class);
    }

    /**
     * 验证订单列表查询会批量加载订单商品并组装分页返回。
     */
    @Test
    void selectListPageBuildsOrderListWithGoodsSnapshots() {
        RedisCache redisCache = mock(RedisCache.class);
        OrderMapper orderMapper = mock(OrderMapper.class);
        IOrderGoodsService orderGoodsService = mock(IOrderGoodsService.class);
        OrderQuerySupport support = new OrderQuerySupport(redisCache, orderMapper, orderGoodsService,
                new OrderValidationSupport());
        Page<Order> page = new Page<>(1, 10);
        Page<Order> orderPage = new Page<>(1, 10);
        orderPage.setRecords(List.of(buildOrder()));
        orderPage.setTotal(21);
        OrderGoods orderGoods = buildOrderGoods();
        when(orderMapper.selectOrderListPage(any(IPage.class), any(Order.class), any()))
                .thenReturn(orderPage);
        when(orderGoodsService.list(org.mockito.ArgumentMatchers.<Wrapper<OrderGoods>>any()))
                .thenReturn(List.of(orderGoods));

        OrderListResVO resVO = support.selectListPage(page, 0, 9L);

        assertEquals(1, resVO.getPage());
        assertEquals(3, resVO.getPages());
        assertEquals(1, resVO.getData().size());
        OrderListDataResVO data = resVO.getData().get(0);
        assertEquals(1L, data.getId());
        assertEquals("ORDER-1", data.getOrderSn());
        assertEquals(new BigDecimal("20.00"), data.getActualPrice());
        assertEquals(1, data.getGoodsList().size());
        assertEquals(100L, data.getGoodsList().get(0).getGoodsId());
        verify(orderGoodsService).list(org.mockito.ArgumentMatchers.<Wrapper<OrderGoods>>any());
    }

    /**
     * 订单归属校验：查询他人订单详情必须被拒绝。
     * 详情里含收货人、手机号、地址，修复前只按订单号查、不校验归属，任何登录用户都能读到。
     */
    @Test
    void getOrderDetailByOrderSnRejectsOrderOwnedByAnotherUser() {
        OrderMapper orderMapper = mock(OrderMapper.class);
        OrderQuerySupport support = new OrderQuerySupport(mock(RedisCache.class), orderMapper,
                mock(IOrderGoodsService.class), new OrderValidationSupport());
        when(orderMapper.selectOne(any())).thenReturn(buildOrder());

        BusinessException exception = assertThrows(BusinessException.class,
                () -> support.getOrderDetailByOrderSn("ORDER-1", 8L));

        assertEquals(ReturnCodeEnum.ORDER_USER_NOT_SAME_ERROR.getCode(), exception.getCode());
    }

    /**
     * 自己的订单能正常拿到详情（归属校验不能误伤正常查询）。
     */
    @Test
    void getOrderDetailByOrderSnReturnsDetailForOwner() {
        OrderMapper orderMapper = mock(OrderMapper.class);
        IOrderGoodsService orderGoodsService = mock(IOrderGoodsService.class);
        OrderQuerySupport support = new OrderQuerySupport(mock(RedisCache.class), orderMapper,
                orderGoodsService, new OrderValidationSupport());
        when(orderMapper.selectOne(any())).thenReturn(buildOrder());
        when(orderGoodsService.list(org.mockito.ArgumentMatchers.<Wrapper<OrderGoods>>any()))
                .thenReturn(List.of(buildOrderGoods()));

        OrderDetailVO detail = support.getOrderDetailByOrderSn("ORDER-1", 9L);

        assertEquals("ORDER-1", detail.getOrderSn());
        assertEquals(9L, detail.getUserId());
        assertEquals(1, detail.getOrderGoodsVOList().size());
    }

    /**
     * 构建订单主表测试数据。
     *
     * @return 订单主表
     */
    private Order buildOrder() {
        Order order = new Order();
        order.setId(1L);
        order.setUserId(9L);
        order.setOrderSn("ORDER-1");
        order.setOrderStatus(OrderStatusEnum.STATUS_CREATE.getStatus());
        order.setActualPrice(new BigDecimal("20.00"));
        return order;
    }

    /**
     * 构建订单商品测试数据。
     *
     * @return 订单商品快照
     */
    private OrderGoods buildOrderGoods() {
        OrderGoods orderGoods = new OrderGoods();
        orderGoods.setOrderId(1L);
        orderGoods.setGoodsId(100L);
        orderGoods.setProductId(1000L);
        orderGoods.setNumber(2);
        return orderGoods;
    }
}
