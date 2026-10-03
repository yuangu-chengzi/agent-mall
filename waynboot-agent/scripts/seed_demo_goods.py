"""往 mall 里补一批演示商品（含规格、SKU、参数、图文详情）。

**为什么需要**：上游的演示数据偏家居杂货，且很多商品没有规格（SKU 列表是空的），
前端"选规格 → 价格/库存跟着变"这条最有技术含量的交互**没有数据可以演示**。
本脚本补 12 个各品类商品，每个都带 2 组规格 × 2~3 个取值 = 4~6 个 SKU。

顺带把两个链路也走通：
  * 商品图片：引用 `/upload/demo/goods-<货号>-*.webp`，由 `gen_demo_images.py` 生成；
  * ES 同步：按项目自己的 outbox 协议写 `GOODS_CHANGED` 本地消息（**没有 MQ 路由，
    由 relay 找本地处理器同步 ES**，见 `GoodsChangedLocalMessageHandler`），
    这样新商品能被搜索到——走的正是 A9 修复后的那条路。

幂等：按 `goods_sn` 判断，已存在就跳过（重复执行安全）。

用法::

    .venv/Scripts/python.exe scripts/seed_demo_goods.py
    .venv/Scripts/python.exe scripts/gen_demo_images.py   # 给新商品生成图片
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pymysql

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DEV_YML = ROOT.parent / "waynboot-common/src/main/resources/application-dev.yml"

#: 演示货号从这里开始（避开上游的 1006001~1181000 区间）
SN_BASE = 2000001

#: 图文详情统一指向共享演示图（见 gen_demo_images.py）
DETAIL_HTML = (
    '<p><img src="/upload/demo/detail-1.webp"/></p>'
    '<p><img src="/upload/demo/detail-3.webp"/></p>'
    '<p><img src="/upload/demo/detail-5.webp"/></p>'
)

# 每个商品：名称 / 类目 / 简介 / 关键词 / 专柜价 / 售价 / 单位 / 规格 / SKU 价格倍数 / 参数
GOODS = [
    {
        "name": "云朵抗菌鹅绒被 冬被加厚",
        "category": 1008008,  # 居家 > 被枕
        "brief": "95% 白鹅绒，蓬松度 800+，冬天一床就够",
        "keywords": "鹅绒被,冬被,加厚,保暖",
        "counter": 1699,
        "retail": 1299,
        "unit": "床",
        "specs": [("尺寸", ["150×200cm", "200×230cm"]), ("填充量", ["1000g", "1500g"])],
        "deltas": [0, 200, 150, 380],
        "attributes": [("填充物", "95% 白鹅绒"), ("面料", "100 支全棉防绒布"), ("适用季节", "冬季")],
    },
    {
        "name": "天然乳胶枕 护颈记忆棉",
        "category": 1008008,
        "brief": "泰国进口乳胶，回弹慢、不塌陷",
        "keywords": "乳胶枕,护颈,记忆棉,枕头",
        "counter": 399,
        "retail": 259,
        "unit": "只",
        "specs": [("高度", ["低枕 8cm", "高枕 12cm"]), ("面料", ["纯棉外套", "冰丝外套"])],
        "deltas": [0, 40, 20, 60],
        "attributes": [("乳胶含量", "93%"), ("硬度", "中等"), ("尺寸", "60×40cm")],
    },
    {
        "name": "纳帕纹真皮沙发 三人位",
        "category": 1015000,  # 居家 > 家具
        "brief": "头层牛皮，实木框架，可拆洗坐垫",
        "keywords": "沙发,真皮,三人位,客厅",
        "counter": 8999,
        "retail": 7299,
        "unit": "件",
        "specs": [("规格", ["三人位 2.1m", "四人位 2.8m"]), ("颜色", ["奶油白", "焦糖棕", "墨绿"])],
        "deltas": [0, 1800, 0, 120, 260, 120],
        "attributes": [("材质", "头层黄牛皮"), ("框架", "北欧白蜡木"), ("坐感", "偏软")],
    },
    {
        "name": "北欧原木餐桌 1.4 米",
        "category": 1015000,
        "brief": "白蜡木实木，圆角设计，四口之家刚好",
        "keywords": "餐桌,原木,实木,北欧",
        "counter": 2999,
        "retail": 2199,
        "unit": "张",
        "specs": [("尺寸", ["1.2m", "1.4m", "1.6m"])],
        "deltas": [0, 300, 600],
        "attributes": [("材质", "白蜡木"), ("台面厚度", "24mm"), ("安装", "需自行安装（含工具）")],
    },
    {
        "name": "雪平锅不粘奶锅 18cm",
        "category": 1005007,  # 餐厨 > 锅具
        "brief": "日式雪平锅，煮面热奶都好用",
        "keywords": "雪平锅,奶锅,不粘锅,18cm",
        "counter": 229,
        "retail": 159,
        "unit": "口",
        "specs": [("口径", ["16cm", "18cm", "20cm"]), ("带盖", ["带盖", "不带盖"])],
        "deltas": [0, 30, 60, 0, 30, 60],
        "attributes": [
            ("材质", "铝合金 + 不粘涂层"),
            ("适用灶具", "燃气灶 / 电磁炉"),
            ("是否可洗碗机", "是"),
        ],
    },
    {
        "name": "德式不锈钢厨师刀 8 寸",
        "category": 1013005,  # 餐厨 > 刀剪砧板
        "brief": "德国 1.4116 钢材，锋利耐用",
        "keywords": "厨师刀,不锈钢,切片刀,8寸",
        "counter": 499,
        "retail": 349,
        "unit": "把",
        "specs": [("刀刃长度", ["20cm", "23cm"])],
        "deltas": [0, 80],
        "attributes": [("钢材", "1.4116 德标不锈钢"), ("硬度", "HRC 56±2"), ("刀柄", "实木 + 铆钉")],
    },
    {
        "name": "纯棉针织圆领 T 恤",
        "category": 1015001,  # 服装 > T恤
        "brief": "220g 重磅纯棉，不透不塌",
        "keywords": "T恤,纯棉,圆领,短袖,基础款",
        "counter": 199,
        "retail": 99,
        "unit": "件",
        "specs": [("尺码", ["S", "M", "L", "XL"]), ("颜色", ["白色", "黑色"])],
        "deltas": [0, 0, 10, 20, 0, 0, 10, 20],
        "attributes": [("面料", "100% 新疆长绒棉"), ("克重", "220g/㎡"), ("版型", "微宽松")],
    },
    {
        "name": "轻薄鹅绒羽绒服",
        "category": 1020009,  # 服装 > 外衣
        "brief": "800 蓬松度，可收纳进随身小包",
        "keywords": "羽绒服,轻薄,鹅绒,户外,保暖",
        "counter": 1299,
        "retail": 899,
        "unit": "件",
        "specs": [("尺码", ["M", "L", "XL"]), ("颜色", ["曜石黑", "雾霾蓝", "奶咖"])],
        "deltas": [0, 0, 0, 0, 0, 0, 0, 0, 0],
        "attributes": [("填充物", "90% 白鹅绒"), ("蓬松度", "800+"), ("防水等级", "防泼水")],
    },
    {
        "name": "婴儿有机棉连体衣",
        "category": 1020003,  # 婴童 > 服饰
        "brief": "A 类婴幼儿标准，无荧光剂",
        "keywords": "婴儿,连体衣,有机棉,爬服",
        "counter": 159,
        "retail": 89,
        "unit": "件",
        "specs": [("身高", ["59cm", "66cm", "73cm"]), ("颜色", ["米白", "浅蓝"])],
        "deltas": [0, 10, 20, 0, 10, 20],
        "attributes": [("安全等级", "A 类婴幼儿用品"), ("面料", "100% 有机棉"), ("洗涤", "可机洗 40℃")],
    },
    {
        "name": "冷萃冻干咖啡 12 颗装",
        "category": 1005013,  # 饮食 > 冲饮
        "brief": "冷萃冻干工艺，冷水三秒即溶",
        "keywords": "咖啡,冻干,冷萃,速溶,黑咖啡",
        "counter": 168,
        "retail": 119,
        "unit": "盒",
        "specs": [("口味", ["经典美式", "浓郁意式"]), ("规格", ["12 颗", "24 颗"])],
        "deltas": [0, 0, 89, 89],
        "attributes": [("工艺", "冷萃 + 冻干"), ("烘焙度", "中深烘"), ("保质期", "18 个月")],
    },
    {
        "name": "氨基酸温和洁面乳",
        "category": 1013001,  # 洗护
        "brief": "弱酸性配方，敏感肌也能天天用",
        "keywords": "洁面乳,氨基酸,温和,洗面奶",
        "counter": 129,
        "retail": 79,
        "unit": "支",
        "specs": [("规格", ["100g", "150g"])],
        "deltas": [0, 40],
        "attributes": [("清洁成分", "氨基酸表活"), ("pH 值", "5.5 弱酸性"), ("适用肤质", "干性 / 敏感肌")],
    },
    {
        "name": "便携露营折叠椅",
        "category": 1008005,  # 杂货 > 户外
        "brief": "铝合金骨架，承重 120kg，收起只有矿泉水大小",
        "keywords": "露营,折叠椅,户外,便携",
        "counter": 299,
        "retail": 189,
        "unit": "把",
        "specs": [("颜色", ["军绿", "沙色", "黑色"]), ("款型", ["标准款", "加宽款"])],
        "deltas": [0, 0, 0, 40, 40, 40],
        "attributes": [("骨架", "7075 铝合金"), ("承重", "120kg"), ("净重", "0.9kg")],
    },
]


#: local_message 有四个 NOT NULL 且无默认值的列，裸 SQL 插入必须自己带上
OUTBOX_SQL = (
    "insert into local_message"
    "(message_key,topic,biz_type,biz_id,payload,next_retry_time,create_time,update_time) "
    "values (%s,'GOODS_CHANGED','GOODS',%s,%s,%s,%s,%s)"
)


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
    parser = argparse.ArgumentParser(description="补一批带 SKU 的演示商品")
    parser.add_argument("--clean", action="store_true", help="先删掉本脚本上次插入的商品再插")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if args.clean:
        cur.execute("select id from shop_goods where goods_sn like '2%' and id >= %s", (SN_BASE,))
        ids = [r[0] for r in cur.fetchall()]
        for gid in ids:
            for table in (
                "shop_goods",
                "shop_goods_product",
                "shop_goods_specification",
                "shop_goods_attribute",
            ):
                cur.execute(
                    f"delete from {table} where " + ("id" if table == "shop_goods" else "goods_id") + "=%s",
                    (gid,),
                )
            cur.execute(
                OUTBOX_SQL,
                (
                    f"GOODS_CHANGED:{gid}:{int(datetime.now().timestamp() * 1000)}",
                    str(gid),
                    json.dumps({"goodsId": gid}),
                    now,
                    now,
                    now,
                ),
            )
        conn.commit()
        print(f"已清理 {len(ids)} 个演示商品（并投递删除同步）")

    created, skipped = [], 0
    for i, g in enumerate(GOODS):
        sn = str(SN_BASE + i)
        cur.execute("select id from shop_goods where goods_sn=%s", (sn,))
        if cur.fetchone():
            skipped += 1
            continue

        gallery = [f"/upload/demo/goods-{sn}-{n}.webp" for n in range(1, 4)]
        cur.execute(
            """insert into shop_goods
               (goods_sn,name,category_id,brand_id,gallery,keywords,brief,is_on_sale,sort,pic_url,share_url,
                is_new,is_hot,unit,counter_price,retail_price,actual_sales,virtual_sales,detail,create_time,update_time,del_flag)
               values (%s,%s,%s,0,%s,%s,%s,1,%s,%s,'',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)""",
            (
                sn,
                g["name"],
                g["category"],
                json.dumps(gallery, ensure_ascii=False),
                g["keywords"],
                g["brief"],
                100 + i,
                f"/upload/demo/goods-{sn}-0.webp",
                1 if i % 3 == 0 else 0,
                1 if i % 4 == 0 else 0,
                g["unit"],
                g["counter"],
                g["retail"],
                i % 7,
                20 + i * 3,
                DETAIL_HTML,
                now,
                now,
            ),
        )
        goods_id = cur.lastrowid

        # 规格值与 SKU：按"每组规格的各取值组合"笛卡尔积生成
        value_groups = []
        for spec_name, values in g["specs"]:
            for v in values:
                cur.execute(
                    "insert into shop_goods_specification"
                    "(goods_id,specification,value,pic_url,create_time,update_time,del_flag) "
                    "values (%s,%s,%s,'',%s,%s,0)",
                    (goods_id, spec_name, v, now, now),
                )
            value_groups.append(values)

        combos = [[]]
        for values in value_groups:
            combos = [c + [v] for c in combos for v in values]
        for idx, combo in enumerate(combos):
            price = g["retail"] + g["deltas"][idx % len(g["deltas"])]
            stock = 20 + (idx * 17) % 180
            cur.execute(
                "insert into shop_goods_product"
                "(goods_id,specifications,price,number,locked_stock,default_selected,version,url,"
                "create_time,update_time,del_flag)"
                " values (%s,%s,%s,%s,0,%s,0,%s,%s,%s,0)",
                (
                    goods_id,
                    json.dumps(combo, ensure_ascii=False),
                    price,
                    stock,
                    1 if idx == 0 else 0,
                    f"/upload/demo/goods-{sn}-{idx % 3 + 1}.webp",
                    now,
                    now,
                ),
            )

        for attr, value in g["attributes"]:
            cur.execute(
                "insert into shop_goods_attribute"
                "(goods_id,attribute,value,create_time,update_time,del_flag) values (%s,%s,%s,%s,%s,0)",
                (goods_id, attr, value, now, now),
            )

        # 商品变更 → outbox（无 MQ 路由 → relay 找本地处理器同步 ES，这正是 A9 修好的那条路）
        cur.execute(
            OUTBOX_SQL,
            (
                f"GOODS_CHANGED:{goods_id}:{int(datetime.now().timestamp() * 1000)}",
                str(goods_id),
                json.dumps({"goodsId": goods_id}),
                now,
                now,
                now,
            ),
        )
        created.append((goods_id, g["name"], len(combos)))

    conn.commit()
    print(f"新增 {len(created)} 个商品（跳过已存在 {skipped} 个）：")
    for goods_id, name, sku in created:
        print(f"  id={goods_id}  {name}  ({sku} 个 SKU)")
    if created:
        print("\n下一步：跑 gen_demo_images.py 给新商品生成图片；relay 会在 5 秒内把它们同步进 ES。")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
