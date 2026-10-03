package com.wayn.mobile.api.controller.callback;

import com.alibaba.fastjson.JSON;
import com.wayn.domain.api.trade.service.IMobileOrderService;
import com.wayn.data.redis.constant.RedisKeyEnum;
import com.wayn.data.redis.manager.RedisCache;
import com.wayn.message.core.dto.OrderDTO;
import com.wayn.util.exception.BusinessException;
import com.wayn.util.util.R;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 下单回调接口
 */
@Slf4j
@RestController
@AllArgsConstructor
@RequestMapping("callback/order")
public class SubmitOrderController {

    private IMobileOrderService iMobileOrderService;
    private RedisCache redisCache;

    /**
     * 回调下单
     *
     * @param order 订单数据传输对象
     * @return R
     */
    @PostMapping("submit")
    public R submit(String order) {
        OrderDTO orderDTO = JSON.parseObject(order, OrderDTO.class);
        // 结果文案与归属一起写：轮询接口只有订单号，靠归属判断"这单是不是你的"（同 OrderSubmitSupport#cacheSubmitResult）
        try {
            iMobileOrderService.submit(orderDTO);
            cacheSubmitResult(orderDTO.getOrderSn(), orderDTO.getUserId(), "success");
            log.info("回调下单完成, orderSn={}, userId={}, result=success", orderDTO.getOrderSn(), orderDTO.getUserId());
            return R.success();
        } catch (Exception e) {
            String errorMsg = "error";
            if (e instanceof BusinessException businessException) {
                errorMsg = businessException.getMsg();
            }
            cacheSubmitResult(orderDTO.getOrderSn(), orderDTO.getUserId(), errorMsg);
            log.error("回调下单失败, orderSn={}, userId={}, message={}", orderDTO.getOrderSn(), orderDTO.getUserId(), errorMsg, e);
            return R.error();
        }
    }


    /**
     * 写下单结果与归属用户。
     *
     * @param orderSn 订单号
     * @param userId 用户 ID
     * @param value 结果文案
     */
    private void cacheSubmitResult(String orderSn, Long userId, String value) {
        redisCache.setCacheObject(RedisKeyEnum.ORDER_RESULT_KEY.getKey(orderSn),
                value, RedisKeyEnum.ORDER_RESULT_KEY.getExpireSecond());
        if (userId != null) {
            // 存字符串：FastJSON 会把裸数字读回 Integer，声明成 Long 会 ClassCastException。
            redisCache.setCacheObject(RedisKeyEnum.ORDER_RESULT_OWNER_KEY.getKey(orderSn),
                    String.valueOf(userId), RedisKeyEnum.ORDER_RESULT_OWNER_KEY.getExpireSecond());
        }
    }
}
