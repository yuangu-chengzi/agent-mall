package com.wayn.domain.promotion.support.seckill;

import com.wayn.data.redis.manager.RedisCache;
import com.wayn.domain.api.promotion.enums.SeckillResultStatusEnum;
import com.wayn.domain.api.promotion.response.SeckillResultResVO;
import com.wayn.util.enums.ReturnCodeEnum;
import com.wayn.util.exception.BusinessException;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.serializer.SerializationException;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 秒杀结果缓存查询测试。
 */
@ExtendWith(MockitoExtension.class)
class SeckillResultSupportTest {

    private static final Long USER_ID = 7L;
    private static final Long OTHER_USER_ID = 8L;

    @Mock
    private RedisCache redisCache;

    /**
     * 构建"缓存里已有的结果"JSON：FastJSON 写入的形态。
     */
    private String cachedResult(Long ownerId) {
        return "{\"orderSn\":\"ORDER-2\",\"status\":\"SUCCESS\",\"actualPrice\":59.70,\"userId\":" + ownerId + "}";
    }

    /**
     * 归属校验：结果里含实付金额，不是自己的订单一律拒绝。
     */
    @Test
    void getResultRejectsOtherUsersOrder() {
        when(redisCache.getCacheObject(SeckillRedisKeySupport.resultKey("ORDER-2")))
                .thenReturn(cachedResult(OTHER_USER_ID));

        BusinessException exception = assertThrows(BusinessException.class,
                () -> new SeckillResultSupport(redisCache).getResult("ORDER-2", USER_ID));

        assertEquals(ReturnCodeEnum.ORDER_USER_NOT_SAME_ERROR.getCode(), exception.getCode());
    }

    /**
     * 自己的订单正常返回（别把正常查询也拦了）。
     */
    @Test
    void getResultReturnsOwnResult() {
        when(redisCache.getCacheObject(SeckillRedisKeySupport.resultKey("ORDER-2")))
                .thenReturn(cachedResult(USER_ID));

        SeckillResultResVO result = new SeckillResultSupport(redisCache).getResult("ORDER-2", USER_ID);

        assertEquals("SUCCESS", result.getStatus());
        assertEquals(USER_ID, result.getUserId());
    }

    /**
     * 历史缓存没有归属信息时放行（那种数据只剩状态文案，不含用户数据；
     * 严格拒绝会把升级前写入的在途结果全判成越权）。
     */
    @Test
    void getResultAllowsLegacyResultWithoutOwner() {
        when(redisCache.getCacheObject(SeckillRedisKeySupport.resultKey("ORDER-3")))
                .thenReturn("{\"orderSn\":\"ORDER-3\",\"status\":\"PROCESSING\"}");

        SeckillResultResVO result = new SeckillResultSupport(redisCache).getResult("ORDER-3", OTHER_USER_ID);

        assertEquals(SeckillResultStatusEnum.PROCESSING.name(), result.getStatus());
    }

    /**
     * 写入结果时必须带上归属，否则读取端无从校验。
     */
    @Test
    void markSuccessCachesOwner() {
        new SeckillResultSupport(redisCache).markSuccess("ORDER-4", new BigDecimal("59.70"), USER_ID);

        ArgumentCaptor<String> captor = ArgumentCaptor.forClass(String.class);
        verify(redisCache).setCacheObject(anyString(), captor.capture(), anyInt());

        assertTrue(captor.getValue().contains("\"userId\":" + USER_ID), captor.getValue());
    }

    /**
     * 兼容历史 Lua 写入的裸 PROCESSING 值，避免 FastJSON 反序列化失败直接打断轮询接口。
     */
    @Test
    void getResultReturnsProcessingWhenLegacyRawStatusCannotDeserialize() {
        when(redisCache.getCacheObject(SeckillRedisKeySupport.resultKey("ORDER-1")))
                .thenThrow(new SerializationException("Could not deserialize: PROCESSING"));

        SeckillResultResVO result = new SeckillResultSupport(redisCache).getResult("ORDER-1", USER_ID);

        assertEquals("ORDER-1", result.getOrderSn());
        assertEquals(SeckillResultStatusEnum.PROCESSING.name(), result.getStatus());
    }
}
