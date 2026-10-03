package com.wayn.domain.promotion.support.coupon;

import cn.hutool.core.date.DateUtil;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.wayn.domain.api.promotion.entity.ShopCoupon;
import com.wayn.domain.api.promotion.entity.ShopMemberCoupon;
import com.wayn.domain.api.promotion.mapper.ShopCouponMapper;
import com.wayn.domain.api.promotion.service.ShopMemberCouponService;
import com.wayn.domain.api.common.TradeLockSupport;
import com.wayn.domain.api.promotion.request.CouponReceiveReqVO;
import com.wayn.domain.api.promotion.request.ShopCouponGiveUserReqVO;
import com.wayn.data.redis.constant.RedisKeyEnum;
import com.wayn.util.enums.ReturnCodeEnum;
import com.wayn.util.exception.BusinessException;
import lombok.AllArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.Date;

/**
 * 优惠券领取支撑服务。
 * 负责用户领券和后台发券流程，并通过用户维度锁和条件更新控制并发超发。
 */
@Service
@AllArgsConstructor
public class CouponAcquireSupport {

    /** 领券锁 TTL（秒）：需覆盖"校验 + 条件更新 + 落库"的事务时长，30 秒足够；续期由看门狗按 TTL/3 负责。 */
    static final int COUPON_LOCK_TTL_SECONDS = 30;

    private final ShopCouponMapper shopCouponMapper;
    private final ShopMemberCouponService shopMemberCouponService;
    private final TradeLockSupport tradeLockSupport;
    private final ShopMemberCouponAssembler shopMemberCouponAssembler;
    private final TransactionTemplate transactionTemplate;

    /**
     * 后台给指定用户发券。
     *
     * @param reqVO 发券请求
     */
    public void giveUser(ShopCouponGiveUserReqVO reqVO) {
        ShopCoupon shopCoupon = shopCouponMapper.selectById(reqVO.getCouponId());
        validateCouponExists(shopCoupon);
        if (DateUtil.compare(shopCoupon.getExpireTime(), new Date()) < 0) {
            throw new BusinessException(ReturnCodeEnum.ERROR.getCode(), "优惠券已经过期");
        }
        shopMemberCouponService.save(shopMemberCouponAssembler.build(shopCoupon, reqVO.getUserId()));
    }

    /**
     * 用户主动领券。
     * 用户和券模板维度会加锁，并通过模板剩余数量条件更新防止并发超领。
     * 事务边界说明：事务在【锁内】开启并提交（见 executeReceive），保证"提交先于放锁"；
     * 修复前事务包裹整个方法、锁在方法体内，会出现"先放锁、后提交"的窗口——
     * 期间其他请求读不到未提交的领取记录，"已领过"校验会被绕过。
     *
     * @param reqVO 领券请求
     * @param userId 用户 ID
     * @return 是否领取成功
     */
    public Boolean receive(CouponReceiveReqVO reqVO, Long userId) {
        Integer couponId = reqVO.getCouponId();
        String lockKey = RedisKeyEnum.COUPON_RECEIVE_LOCK.getKey(userId + ":" + couponId);
        return tradeLockSupport.executeWithLock(lockKey, COUPON_LOCK_TTL_SECONDS,
                () -> new BusinessException(ReturnCodeEnum.ERROR, "领取过于频繁，请稍后重试"),
                () -> executeReceive(reqVO, couponId, userId));
    }

    /**
     * 在锁内开启事务执行领券。
     * transactionTemplate 为空时直接执行（单元测试场景，便于直接验证业务分支）。
     *
     * @param reqVO 领券请求
     * @param couponId 优惠券模板 ID
     * @param userId 用户 ID
     * @return 是否领取成功
     */
    private Boolean executeReceive(CouponReceiveReqVO reqVO, Integer couponId, Long userId) {
        if (transactionTemplate == null) {
            return doReceive(reqVO, couponId, userId);
        }
        return transactionTemplate.execute(status -> doReceive(reqVO, couponId, userId));
    }

    /**
     * 领券业务逻辑（校验 + 条件更新 + 落库），由 executeReceive 在事务内调用。
     *
     * @param reqVO 领券请求
     * @param couponId 优惠券模板 ID
     * @param userId 用户 ID
     * @return 是否领取成功
     */
    private Boolean doReceive(CouponReceiveReqVO reqVO, Integer couponId, Long userId) {
        ShopCoupon shopCoupon = shopCouponMapper.selectById(couponId);
        validateCouponExists(shopCoupon);
        if (shopCoupon.getStatus() != 1) {
            throw new BusinessException(ReturnCodeEnum.ERROR, "优惠券未上架");
        }
        if (DateUtil.compare(shopCoupon.getExpireTime(), new Date()) < 0) {
            throw new BusinessException(ReturnCodeEnum.ERROR, "优惠券已过期");
        }

        long existsCount = shopMemberCouponService.count(Wrappers.lambdaQuery(ShopMemberCoupon.class)
                .eq(ShopMemberCoupon::getUserId, userId)
                .eq(ShopMemberCoupon::getCouponId, couponId));
        if (existsCount > 0) {
            throw new BusinessException(ReturnCodeEnum.ERROR, "优惠券已经领过了");
        }

        // 数据库条件更新是最后一道防线，只有库存未领完时才允许递增 receive_num。
        int updated = shopCouponMapper.updateReceiveNum(couponId);
        if (updated == 0) {
            throw new BusinessException(ReturnCodeEnum.ERROR, "优惠券已经领完啦");
        }
        return shopMemberCouponService.save(shopMemberCouponAssembler.build(shopCoupon, Math.toIntExact(userId)));
    }

    /**
     * 校验优惠券模板必须存在。
     *
     * @param shopCoupon 优惠券模板
     */
    private void validateCouponExists(ShopCoupon shopCoupon) {
        if (shopCoupon == null) {
            throw new BusinessException(ReturnCodeEnum.ERROR.getCode(), "优惠券不存在");
        }
    }
}
