package com.wayn.domain.promotion.support.seckill;

import com.alibaba.fastjson.JSON;
import com.wayn.data.redis.manager.RedisCache;
import com.wayn.domain.api.promotion.enums.SeckillResultStatusEnum;
import com.wayn.domain.api.promotion.response.SeckillResultResVO;
import com.wayn.util.enums.ReturnCodeEnum;
import com.wayn.util.exception.BusinessException;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.data.redis.serializer.SerializationException;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.Objects;

/**
 * 秒杀结果缓存支撑服务。
 * 秒杀入口异步返回后，前端通过订单号轮询该缓存；消费端落库成功、失败或关单时统一更新结果。
 */
@Slf4j
@Service
@AllArgsConstructor
public class SeckillResultSupport {

    private static final int RESULT_TTL_SECONDS = 3600;

    private final RedisCache redisCache;

    /**
     * 查询秒杀结果。
     *
     * @param orderSn 订单号
     * @param userId 当前登录用户 ID（结果含实付金额，必须校验归属）
     * @return 秒杀结果
     */
    public SeckillResultResVO getResult(String orderSn, Long userId) {
        SeckillResultResVO fallback = buildResult(orderSn, SeckillResultStatusEnum.PROCESSING.name(), null, null, null);
        if (StringUtils.isBlank(orderSn)) {
            return fallback;
        }
        String cached = getCachedResult(orderSn);
        if (StringUtils.isBlank(cached)) {
            return fallback;
        }
        if (cached.trim().startsWith("{")) {
            SeckillResultResVO result = JSON.parseObject(cached, SeckillResultResVO.class);
            validateOwner(orderSn, result.getUserId(), userId);
            return result;
        }
        // 历史 Lua 写入的裸状态值：只有状态文案、不含用户数据，按处理中返回
        return buildResult(orderSn, cached, null, null, null);
    }

    /**
     * 校验秒杀结果的归属。
     * 缓存里没有归属信息时放行：那种情况只剩状态文案（裸字符串/历史数据），不含用户数据；
     * 而"严格拒绝"会把升级前写入的在途结果全判为越权，前端轮询会一直拿不到结果。
     *
     * @param orderSn 订单号
     * @param ownerId 结果归属用户 ID（可能为空）
     * @param userId 当前登录用户 ID
     */
    private void validateOwner(String orderSn, Long ownerId, Long userId) {
        if (ownerId != null && !Objects.equals(ownerId, userId)) {
            log.warn("秒杀结果归属校验失败, orderSn={}", orderSn);
            throw new BusinessException(ReturnCodeEnum.ORDER_USER_NOT_SAME_ERROR);
        }
    }

    /**
     * 读取秒杀结果缓存。
     * 兼容历史 Lua 写入的裸 PROCESSING 值：裸字符串无法被 FastJSON Redis 序列化器反序列化，查询时按处理中返回。
     *
     * @param orderSn 订单号
     * @return 缓存结果字符串
     */
    private String getCachedResult(String orderSn) {
        try {
            return redisCache.getCacheObject(SeckillRedisKeySupport.resultKey(orderSn));
        } catch (SerializationException e) {
            log.warn("秒杀结果缓存反序列化失败，按处理中返回, orderSn={}, message={}", orderSn, e.getMessage());
            return SeckillResultStatusEnum.PROCESSING.name();
        }
    }

    /**
     * 标记秒杀订单创建成功。
     *
     * @param orderSn 订单号
     * @param actualPrice 实付金额
     * @param userId 订单归属用户 ID
     */
    public void markSuccess(String orderSn, BigDecimal actualPrice, Long userId) {
        cacheResult(buildResult(orderSn, SeckillResultStatusEnum.SUCCESS.name(), actualPrice, null, userId));
    }

    /**
     * 标记秒杀订单处理失败。
     *
     * @param orderSn 订单号
     * @param failReason 失败原因
     * @param userId 订单归属用户 ID
     */
    public void markFailed(String orderSn, String failReason, Long userId) {
        cacheResult(buildResult(orderSn, SeckillResultStatusEnum.FAILED.name(), null, failReason, userId));
    }

    /**
     * 标记秒杀订单超时关闭。
     *
     * @param orderSn 订单号
     * @param userId 订单归属用户 ID
     */
    public void markClosed(String orderSn, Long userId) {
        cacheResult(buildResult(orderSn, SeckillResultStatusEnum.CLOSED.name(), null, "超时未支付，订单已关闭", userId));
    }

    /**
     * 写入秒杀结果缓存。
     *
     * @param result 秒杀结果
     */
    private void cacheResult(SeckillResultResVO result) {
        if (StringUtils.isBlank(result.getOrderSn())) {
            return;
        }
        redisCache.setCacheObject(SeckillRedisKeySupport.resultKey(result.getOrderSn()),
                JSON.toJSONString(result), RESULT_TTL_SECONDS);
    }

    /**
     * 构建秒杀结果。
     *
     * @param orderSn 订单号
     * @param status 状态
     * @param actualPrice 实付金额
     * @param failReason 失败原因
     * @return 秒杀结果
     */
    private SeckillResultResVO buildResult(String orderSn, String status, BigDecimal actualPrice, String failReason,
                                           Long userId) {
        SeckillResultResVO resVO = new SeckillResultResVO();
        resVO.setOrderSn(orderSn);
        resVO.setStatus(status);
        resVO.setActualPrice(actualPrice);
        resVO.setFailReason(failReason);
        resVO.setUserId(userId);
        return resVO;
    }
}
