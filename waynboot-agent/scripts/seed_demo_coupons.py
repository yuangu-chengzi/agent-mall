"""补几张演示优惠券，并把它们发到已有账号名下。

**为什么需要**：上游的 SQL 只建了 `shop_coupon` 表、没带一条数据，于是结算页的"优惠券"
恒显示"暂无可用"——这个功能在页面上完全看不到。

三个口径都对着 mall 的真实实现来写（`CartReadSupport.selectAvailableCoupons`）：
  * 可用条件 = 未使用 + 未过期 + `订单金额 >= min`；
  * `discount` / `min` 都是**元**（服务端 `couponPrice = BigDecimal.valueOf(discount)` 直接减）；
  * `type=1` 是注册赠券（`LoginController` 在新建用户时自动发），所以留一张给新手机号，
    `type=2` 是普通券，能在「领券中心」自己领。

幂等：按标题/（用户, 券）判断，重复执行不会重复插入。

用法::

    .venv/Scripts/python.exe scripts/seed_demo_coupons.py
    .venv/Scripts/python.exe scripts/seed_demo_coupons.py --mobile 13800000001   # 只发给某个号
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pymysql

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DEV_YML = ROOT.parent / "waynboot-common/src/main/resources/application-dev.yml"

#: 券模板。门槛特意拉开档次，好让"不同金额能用不同的券"这件事在页面上看得出来。
COUPONS = [
    {"title": "新人专享 · 满 99 减 10", "discount": 10, "min": 99, "type": 1, "num": 100000},
    {"title": "全场通用 · 满 299 减 30", "discount": 30, "min": 299, "type": 2, "num": 5000},
    {"title": "全场通用 · 满 999 减 100", "discount": 100, "min": 999, "type": 2, "num": 2000},
    {"title": "大件专享 · 满 2000 减 250", "discount": 250, "min": 2000, "type": 2, "num": 500},
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
    parser = argparse.ArgumentParser(description="补演示优惠券")
    parser.add_argument("--mobile", help="只发给这个手机号（默认发给所有已有账号）")
    parser.add_argument("--days", type=int, default=180, help="有效期天数")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()
    expire = (datetime.now() + timedelta(days=args.days)).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    coupon_ids = {}
    for c in COUPONS:
        cur.execute("select id from shop_coupon where title=%s", (c["title"],))
        row = cur.fetchone()
        if row:
            coupon_ids[c["title"]] = row[0]
            continue
        cur.execute(
            "insert into shop_coupon"
            "(title,num,receive_num,discount,min,status,type,expire_time,create_time,create_by,del_flag) "
            "values (%s,%s,0,%s,%s,1,%s,%s,%s,'demo',0)",
            (c["title"], c["num"], c["discount"], c["min"], c["type"], expire, now),
        )
        coupon_ids[c["title"]] = cur.lastrowid
        print(f"  新建券：{c['title']}（id={cur.lastrowid}，满 {c['min']} 减 {c['discount']}）")

    if args.mobile:
        cur.execute("select id, mobile from shop_member where mobile=%s", (args.mobile,))
    else:
        cur.execute("select id, mobile from shop_member")
    members = cur.fetchall()
    if not members:
        print("库里还没有会员（先在前端登录一次会自动注册），券已建好但没发出去")
        conn.commit()
        conn.close()
        return 0

    granted = skipped = 0
    for user_id, _mobile in members:
        for c in COUPONS:
            coupon_id = coupon_ids[c["title"]]
            cur.execute(
                "select id from shop_member_coupon where user_id=%s and coupon_id=%s", (user_id, coupon_id)
            )
            if cur.fetchone():
                skipped += 1
                continue
            cur.execute(
                "insert into shop_member_coupon"
                "(user_id,title,coupon_id,discount,min,expire_time,use_status,create_time,del_flag) "
                "values (%s,%s,%s,%s,%s,%s,0,%s,0)",
                (user_id, c["title"], coupon_id, c["discount"], c["min"], expire, now),
            )
            granted += 1

    conn.commit()
    conn.close()
    print(f"券 {len(coupon_ids)} 张；发给 {len(members)} 个账号，新增 {granted} 张（跳过已有 {skipped} 张）")
    print("结算页的「优惠券」现在应该能看到可用券了；新手机号首次登录会自动拿到「新人专享」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
