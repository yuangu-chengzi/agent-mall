package com.wayn.domain.api.promotion.response;

import lombok.Data;

import java.math.BigDecimal;

/**
 * 秒杀结果响应。
 */
@Data
public class SeckillResultResVO {

    private String orderSn;

    private String status;

    private BigDecimal actualPrice;

    private String failReason;

    /**
     * 结果归属的用户 ID。
     * 该结果里含实付金额，属于用户数据，因此随缓存一起写入、查询时校验归属（见 SeckillResultSupport#getResult）。
     */
    private Long userId;
}
