package com.wayn.data.redis.manager;

import cn.hutool.core.util.IdUtil;
import com.wayn.data.redis.util.ThreadUtil;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * redis锁
 */
@Slf4j
@Component
public class RedisLock {

    /**
     * 默认锁过期时间（单位：秒）。
     * 注意单位：Redis 的 EXPIRE 参数是秒；锁 TTL 的作用是"持锁进程崩溃后的自动释放时间"，
     * 续期由看门狗负责（按 TTL/3 周期续期）。
     */
    public static final Integer DEFAULT_TTL_SECONDS = 30;
    @Autowired
    public RedisTemplate redisTemplate;
    /**
     * 保存线程id-ThreadLocal
     */
    private ThreadLocal<String> stringThreadLocal = new ThreadLocal<>();

    /**
     * 保存定时任务（watch-dog）-ThreadLocal
     */
    private ThreadLocal<ExecutorService> executorServiceThreadLocal = new ThreadLocal<>();


    /**
     * 加锁，不指定过期时间
     *
     * @param key key名称
     * @return boolean
     */
    public boolean lock(String key) {
        return lock(key, null);
    }

    /**
     * 加锁
     *
     * @param key            key名称
     * @param timeoutSeconds 锁过期时间（秒），为空或非正数时使用默认值
     * @return boolean
     */
    public boolean lock(String key, Integer timeoutSeconds) {
        // 统一以秒为单位传给 Lua（EXPIRE 的单位是秒），看门狗周期单独换算成毫秒。
        // 修复历史 bug：原实现把传入值再乘 1000 当作秒传给 EXPIRE，导致 TTL 被放大 1000 倍
        // （默认 30 秒 → 30000 秒 ≈ 8.3 小时），进程崩溃后锁长时间无法自动释放。
        int ttlSeconds = timeoutSeconds == null || timeoutSeconds <= 0 ? DEFAULT_TTL_SECONDS : timeoutSeconds;
        long ttlMillis = ttlSeconds * 1000L;
        String nanoId;
        if (stringThreadLocal.get() != null) {
            nanoId = stringThreadLocal.get();
        } else {
            nanoId = IdUtil.nanoId();
            stringThreadLocal.set(nanoId);
        }
        RedisScript<Long> redisScript = new DefaultRedisScript<>(buildLuaLockScript(), Long.class);
        Long execute = (Long) redisTemplate.execute(redisScript, Collections.singletonList(key), nanoId, ttlSeconds);
        boolean flag = execute != null && execute == 1;
        if (flag) {
            ScheduledExecutorService scheduledExecutorService = Executors.newSingleThreadScheduledExecutor();
            executorServiceThreadLocal.set(scheduledExecutorService);
            scheduledExecutorService.scheduleWithFixedDelay(() -> {
                RedisScript<Long> renewRedisScript = new DefaultRedisScript<>(buildLuaRenewScript(), Long.class);
                Long result = (Long) redisTemplate.execute(renewRedisScript, Collections.singletonList(key), nanoId, ttlSeconds);
                if (result != null && result == 2) {
                    ThreadUtil.shutdownAndAwaitTermination(scheduledExecutorService);
                }
            }, ttlMillis / 3, ttlMillis / 3, TimeUnit.MILLISECONDS);
        }
        return flag;
    }

    /**
     * 释放锁
     *
     * @param key key名称
     * @return boolean
     */
    public boolean unLock(final String key) {
        String nanoId = stringThreadLocal.get();
        RedisScript<Long> redisScript = new DefaultRedisScript<>(buildLuaUnLockScript(), Long.class);
        Long execute = (Long) redisTemplate.execute(redisScript, Collections.singletonList(key), nanoId);
        boolean flag = execute != null && execute == 1;
        if (flag) {
            if (executorServiceThreadLocal.get() != null) {
                ThreadUtil.shutdownAndAwaitTermination(executorServiceThreadLocal.get());
            }
        }
        return flag;
    }

    private String buildLuaLockScript() {
        return """
                local key = KEYS[1]
                local value = ARGV[1]
                local time_out = ARGV[2]
                local result = redis.call('get', key)
                if result == value then
                    return 1;
                end
                local lock_result = redis.call('setnx', key, value)
                if tonumber(lock_result) == 1 then
                    redis.call('expire', key, time_out)
                    return 1;
                else
                    return 0;
                end
                """;
    }

    private String buildLuaUnLockScript() {
        return """
                local key = KEYS[1]
                local value = ARGV[1]
                local result = redis.call('get', key)
                if result ~= value then
                    return 0;
                else
                    redis.call('del', key)
                end
                return 1;
                """;
    }

    private String buildLuaRenewScript() {
        return """
                local key = KEYS[1]
                local value = ARGV[1]
                local timeout = ARGV[2]
                local result = redis.call('get', key)
                if result ~= value then
                    return 2;
                end
                local ttl = redis.call('ttl', key)
                if tonumber(ttl) < tonumber(timeout) / 2 then
                    redis.call('expire', key, timeout)
                    return 1;
                else
                    return 0;
                end
                """;
    }
}
