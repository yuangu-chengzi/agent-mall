"""造几个演示秒杀活动（上游 `shop_seckill_activity` / `shop_seckill_sku` 是空表）。

**为什么要脚本**：秒杀页面没数据就是空白，而活动数据在上游库**一条都没有**；
更要紧的是活动库存不能只入库——秒杀扣库存走的是 **Redis 原子预扣**
（`waynboot:seckill:stock:{activitySkuId}`），这个键是管理端「预热」动作写的
（`SeckillActivityServiceImpl.preheat`）。管理端口令拿不到，所以这里**复刻同一套写入**：
先落库，再把可用库存写进 Redis，键名与实现一致。

幂等：按活动名判断，已存在就跳过（重复执行安全）。

用法::

    .venv/Scripts/python.exe scripts/seed_demo_seckill.py
    .venv/Scripts/python.exe scripts/seed_demo_seckill.py --clean   # 先删掉本脚本造的活动
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pymysql
import redis

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DEV_YML = ROOT.parent / "waynboot-common/src/main/resources/application-dev.yml"

#: CacheConstants.CACHE_PREFIX + "seckill:stock:" + activitySkuId（见 SeckillRedisKeySupport）
REDIS_STOCK_KEY = "waynboot:seckill:stock:{}"

#: 秒杀活动状态：1 = 已发布（SeckillActivityStatusEnum.PUBLISHED）
PUBLISHED = 1

ACTIVITIES = [
    # 用演示商品（seed_demo_goods.py 造的），取它的第一个 SKU 做秒杀
    {
        "name": "限时秒杀 · 家居专场",
        "brief": "羽绒被/锅具低至 5 折，每人限购 2 件",
        "sn": "2000001",
        "discount": 0.5,
        "stock": 50,
        "limit": 2,
    },
    {
        "name": "限时秒杀 · 餐厨专场",
        "brief": "雪平锅/刀具，抢完即止",
        "sn": "2000005",
        "discount": 0.6,
        "stock": 30,
        "limit": 1,
    },
    {
        "name": "限时秒杀 · 出行专场",
        "brief": "露营椅特惠，每人限购 1 件",
        "sn": "2000012",
        "discount": 0.55,
        "stock": 20,
        "limit": 1,
    },
]


def connect():
    cfg = DEV_YML.read_text(encoding="utf-8")
    m = re.search(r"url:\s*jdbc:mysql://([\d.]+):(\d+)/(\w+)", cfg)
    return pymysql.connect(
        host=m.group(1),
        port=int(m.group(2)),
        user=re.search(r"username:\s*(\S+)", cfg).group(1),
        password=re.search(r"password:\s*(\S+)", cfg).group(1),
        database=m.group(3),
        charset="utf8mb4",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="造演示秒杀活动")
    parser.add_argument("--clean", action="store_true", help="先删掉本脚本造的活动（含 Redis 键）后再插")
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--days", type=int, default=3, help="活动持续天数")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()
    r = redis.from_url(args.redis_url, socket_connect_timeout=3)
    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    start = (now_dt - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (now_dt + timedelta(days=args.days)).strftime("%Y-%m-%d %H:%M:%S")

    if args.clean:
        for item in ACTIVITIES:
            cur.execute("select id from shop_seckill_activity where name=%s", (item["name"],))
            row = cur.fetchone()
            if not row:
                continue
            aid = row[0]
            cur.execute("select id from shop_seckill_sku where activity_id=%s", (aid,))
            for (sku_id,) in cur.fetchall():
                r.delete(REDIS_STOCK_KEY.format(sku_id))
            cur.execute("delete from shop_seckill_sku where activity_id=%s", (aid,))
            cur.execute("delete from shop_seckill_activity where id=%s", (aid,))
            print(f"  删除活动 {item['name']}（id={aid}）")
        conn.commit()

    created = 0
    for item in ACTIVITIES:
        cur.execute("select id from shop_seckill_activity where name=%s", (item["name"],))
        if cur.fetchone():
            print(f"  跳过已存在：{item['name']}")
            continue

        cur.execute("select id, name, pic_url, retail_price from shop_goods where goods_sn=%s", (item["sn"],))
        goods = cur.fetchone()
        if not goods:
            print(f"  [x] 找不到商品 sn={item['sn']}（先跑 seed_demo_goods.py）")
            continue
        goods_id, goods_name, goods_pic, retail_price = goods

        cur.execute(
            "select id, specifications, price, number, url from shop_goods_product "
            "where goods_id=%s order by id limit 1",
            (goods_id,),
        )
        sku = cur.fetchone()
        if not sku:
            print(f"  [x] 商品 {goods_name} 没有 SKU，跳过")
            continue
        product_id, specs, price, number, sku_pic = sku

        cur.execute(
            "insert into shop_seckill_activity"
            "(name,brief,start_time,end_time,status,sort_order,create_time,update_time,del_flag) "
            "values (%s,%s,%s,%s,%s,100,%s,%s,0)",
            (item["name"], item["brief"], start, end, PUBLISHED, now, now),
        )
        activity_id = cur.lastrowid

        seckill_price = round(float(retail_price) * item["discount"], 2)
        cur.execute(
            "insert into shop_seckill_sku"
            "(activity_id,goods_id,product_id,seckill_price,available_stock,locked_stock,"
            "sold_stock,limit_count,status,create_time,update_time,del_flag) "
            "values (%s,%s,%s,%s,%s,0,0,%s,%s,%s,%s,0)",
            (
                activity_id,
                goods_id,
                product_id,
                seckill_price,
                item["stock"],
                item["limit"],
                PUBLISHED,
                now,
                now,
            ),
        )
        sku_id = cur.lastrowid
        # 复刻管理端"预热"：把可用库存写进 Redis，秒杀下单直接在 Redis 上原子扣
        r.set(REDIS_STOCK_KEY.format(sku_id), item["stock"])
        print(
            f"  新建活动 {item['name']}（id={activity_id}）"
            f"秒杀 SKU id={sku_id} 价格 {seckill_price} 库存 {item['stock']}（已写 Redis）"
        )
        created += 1

    conn.commit()
    conn.close()
    print(f"完成：新增 {created} 个活动。前端入口在首页/分类页的秒杀页（#/seckill）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
