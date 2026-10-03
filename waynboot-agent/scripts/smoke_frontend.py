"""商城前端冒烟脚本：把浏览器要发的请求原样打一遍，检查每个页面依赖的数据形状。

**它验的是"页面的契约"，不是"页面好不好看"**——DOM 与样式需要人眼，但下面这些
（参数名写错、信封没拆、字段是 null、字段名对不上）都能在浏览器之外被抓出来：

1. 静态资源：`/`（商城壳）、`/chat`（客服页）、`/ui/js/*.js` 的 MIME 必须是
   `application/javascript`——**ES Module 的 MIME 不对浏览器会直接拒绝加载**；
2. 经 `/api/mall/**` 代理的真实接口：首页/分类/搜索/详情/购物车/地址/订单；
3. 商品详情的 SKU 结构（`specificationList[i].valueList` 与 `productList[i].specifications` 的对应关系）；
4. A15/A16 的归属校验在**代理路径上**同样生效（换个用户读别人的订单必须失败）；
5. 代理白名单：mall 的非商城接口不能被代理出去。

用法（前置：mall 82、Redis、Agent 8300 都在跑）::

    .venv/Scripts/python.exe scripts/smoke_frontend.py

脚本会往购物车/地址里写数据，结束时自己清理；退出码 0=全过，1=有失败。
"""

from __future__ import annotations

import os
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

BASE_URL = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8300")
MOBILE = os.environ.get("SMOKE_MOBILE", "13800000001")
OTHER_MOBILE = os.environ.get("SMOKE_OTHER_MOBILE", "13900000002")
TIMEOUT = 20.0

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"{'[OK]  ' if passed else '[FAIL]'} {name}" + (f" -- {detail}" if detail else ""), flush=True)


def seed_code(mobile: str) -> bool:
    """开发环境直接把验证码种进 Redis（`waynboot:mobile_code:{mobile}_1234` = "1"）。

    **为什么不用 `/api/login/code`**：mall 对同号码 60 秒内只允许发一次，
    脚本连着跑第二遍就会被限流，然后因为"验证码已被上一条登录消费掉"而登录失败——
    看起来像"登录坏了"，其实是脚本踩了自己的限流。开发环境的验证码是固定的，
    直接种 key 既快又不依赖短信通道；Redis 连不上时返回 False，调用方退回去走真接口。
    """
    try:
        import redis  # 项目依赖里自带（Agent 用它做会话记忆）

        url = os.environ.get("AGENT_REDIS_URL", "redis://localhost:6379/0")
        client = redis.from_url(url, socket_connect_timeout=2)
        client.set(f"waynboot:mobile_code:{mobile}_1234", '"1"', ex=600)
        client.close()
        return True
    except Exception:
        return False


def login(client: httpx.Client, mobile: str) -> str:
    """走 Agent 的登录代理拿 Sa-Token（和页面完全同一条路）。"""
    if not seed_code(mobile):
        seed = client.post(f"{BASE_URL}/api/login/code", json={"mobile": mobile})
        if seed.status_code != 200:
            raise RuntimeError(f"发验证码失败：{seed.status_code} {seed.text[:120]}")
    res = client.post(f"{BASE_URL}/api/login", json={"mobile": mobile, "yzm": "1234"})
    res.raise_for_status()
    return res.json()["token"]


def get(client: httpx.Client, path: str, token: str, **kwargs):
    return client.get(f"{BASE_URL}/api/mall{path}", headers={"Authorization": token}, **kwargs)


def main() -> int:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
        # ---------- 1. 静态资源 ----------
        shell = client.get(f"{BASE_URL}/")
        record(
            "GET / 返回商城壳（含 /ui/js/app.js）",
            shell.status_code == 200 and "/ui/js/app.js" in shell.text,
            f"HTTP {shell.status_code}",
        )

        chat = client.get(f"{BASE_URL}/chat")
        record(
            "GET /chat 返回客服页",
            chat.status_code == 200 and "/api/chat/stream" in chat.text,
            f"HTTP {chat.status_code}",
        )

        js = client.get(f"{BASE_URL}/ui/js/app.js")
        ctype = js.headers.get("content-type", "")
        record(
            "静态 JS 的 MIME 是 application/javascript",
            js.status_code == 200 and "javascript" in ctype,
            ctype or f"HTTP {js.status_code}",
        )

        # ---------- 2. 登录 ----------
        token = login(client, MOBILE)
        record("登录代理拿到 Sa-Token", bool(token), f"{token[:8]}…")

        # ---------- 3. 首页 ----------
        home = get(client, "/home/index", token).json()
        record(
            "首页轮播/金刚区有数据",
            home.get("code") == 200 and (home["data"].get("bannerList") or home["data"].get("diamondList")),
            "banner={} diamond={}".format(
                len(home["data"].get("bannerList") or []), len(home["data"].get("diamondList") or [])
            ),
        )

        rec = get(client, "/home/recommonGoodsList", token, params={"pageNum": 1, "pageSize": 2}).json()
        goods = rec.get("data") or []
        record(
            "推荐商品字段齐全（卡片要用的 id/name/picUrl/retailPrice）",
            bool(goods) and {"id", "name", "picUrl", "retailPrice"} <= set(goods[0]),
            f"{len(goods)} 条",
        )
        goods_id = goods[0]["id"] if goods else None

        # ---------- 4. 分类 ----------
        cate = get(client, "/category/index", token).json()["data"]
        top = (cate.get("categoryList") or [{}])[0]
        record("分类页一级分类非空", bool(top.get("id")), f"{top.get('text')}(id={top.get('id')})")

        content = get(client, "/category/content", token, params={"id": top["id"]}).json()["data"]
        record(
            "分类详情返回二级分类",
            "currentCategory" in content and isinstance(content.get("subCategoryList"), list),
            f"二级 {len(content.get('subCategoryList') or [])} 个",
        )

        first = get(
            client,
            "/category/firstCategoryGoods",
            token,
            params={"cateId": top["id"], "pageNum": 1, "pageSize": 2},
        ).json()
        record(
            "一级分类商品列表可用",
            first.get("code") == 200 and "goods" in first["data"],
            f"{len(first['data'].get('goods') or [])} 件",
        )

        # ---------- 5. 搜索（走 ES） ----------
        search = get(
            client, "/search/result", token, params={"keyword": "床垫", "pageNum": 1, "pageSize": 2}
        ).json()
        record(
            "搜索结果（中文关键词经代理不乱码）",
            search.get("code") == 200 and isinstance(search.get("data"), list),
            f"{len(search.get('data') or [])} 条",
        )
        hot = get(client, "/search/hotKeywords", token).json()
        record(
            "热门搜索词可用",
            hot.get("code") == 200,
            f"{len((hot.get('data') or {}).get('hotStrings') or [])} 个",
        )

        # ---------- 6. 商品详情 + SKU ----------
        detail = get(client, f"/goods/detail/{goods_id}", token).json()["data"]
        info = detail.get("info") or {}
        specs = detail.get("specificationList") or []
        products = detail.get("productList") or []
        record("商品详情 info 可用", bool(info.get("name")), str(info.get("name"))[:20])

        sku_ok = True
        if specs and products:
            # 页面靠"已选规格值数组 == product.specifications"匹配 SKU，顺序必须一致
            values = [v["value"] for v in specs[0]["valueList"]]
            matched = [p for p in products if p["specifications"][0] in values]
            sku_ok = bool(matched) and all(len(p["specifications"]) == len(specs) for p in products)
        record("SKU 数据结构可用（对齐规格顺序）", sku_ok, f"{len(specs)} 组规格 / {len(products)} 个货品")

        # ---------- 7. 购物车 ----------
        added = None
        if specs and products:
            product = products[0]
            r = client.post(
                f"{BASE_URL}/api/mall/cart",
                json={"goodsId": goods_id, "productId": product["id"], "number": 1},
                headers={"Authorization": token},
            )
            added = r.json().get("code") == 200
        else:
            r = client.post(
                f"{BASE_URL}/api/mall/cart/addDefaultGoodsProduct",
                json={"goodsId": goods_id, "number": 1},
                headers={"Authorization": token},
            )
            added = r.json().get("code") == 200
        record("加入购物车成功", bool(added), f"goodsId={goods_id}")

        cart = get(client, "/cart/list", token, params={"pageNum": 1, "pageSize": 20}).json()
        items = cart.get("data") or []
        mine = [i for i in items if i["goodsId"] == goods_id]
        record(
            "购物车列表里有刚加的商品",
            bool(mine),
            f"共 {len(items)} 行",
        )
        cart_id = mine[-1]["id"] if mine else None

        if cart_id:
            checked = client.put(
                f"{BASE_URL}/api/mall/cart",
                json={"id": cart_id, "checked": True},
                headers={"Authorization": token},
            ).json()
            record("勾选状态可写", checked.get("code") == 200, str(checked.get("data")))

            num = client.post(
                f"{BASE_URL}/api/mall/cart/changeNum/{cart_id}/2", headers={"Authorization": token}
            ).json()
            record("改数量可用", num.get("code") == 200, str(num.get("data")))

        summary = client.post(
            f"{BASE_URL}/api/mall/cart/getCheckedGoods", headers={"Authorization": token}
        ).json()
        data = summary.get("data") or {}
        record(
            "结算汇总字段齐全（清单/金额/可用券）",
            summary.get("code") == 200
            and isinstance(data.get("data"), list)
            and "goodsAmount" in data
            and "orderTotalAmount" in data
            and "couponList" in data,
            f"已选 {len(data.get('data') or [])} 件 / 应付 {data.get('orderTotalAmount')}",
        )

        # ---------- 8. 地址 ----------
        created = client.post(
            f"{BASE_URL}/api/mall/address",
            json={
                "name": "前端冒烟",
                "tel": MOBILE,
                "province": "江西省",
                "city": "南昌市",
                "county": "红谷滩区",
                "addressDetail": "冒烟测试地址（脚本会自动删除）",
                "isDefault": False,
            },
            headers={"Authorization": token},
        ).json()
        addr_list = get(client, "/address/list", token).json()
        mine_addr = [
            a for a in (addr_list.get("data") or []) if a.get("addressDetail", "").startswith("冒烟测试地址")
        ]
        record(
            "新增地址并能在列表里查到",
            created.get("code") == 200 and bool(mine_addr),
            f"共 {len(addr_list.get('data') or [])} 条",
        )

        # ---------- 9. 订单 ----------
        orders = get(client, "/order/list", token, params={"showType": 0, "pageNum": 1, "pageSize": 3}).json()
        odata = orders.get("data") or {}
        first_order = (odata.get("data") or [None])[0]
        record(
            "订单列表是双层 data（{data, pages, page}）",
            orders.get("code") == 200 and isinstance(odata.get("data"), list) and "pages" in odata,
            f"{len(odata.get('data') or [])} 笔 / 共 {odata.get('pages')} 页",
        )

        counts = client.post(
            f"{BASE_URL}/api/mall/order/statusCount", headers={"Authorization": token}
        ).json()
        record(
            "订单状态统计可用于角标",
            counts.get("code") == 200
            and {"unpaid", "unship", "unrecv", "uncomment"} <= set(counts.get("data") or {}),
            str(counts.get("data")),
        )

        if first_order:
            detail_order = get(client, f"/order/detail/{first_order['orderSn']}", token).json()
            vo = detail_order.get("data") or {}
            record(
                "订单详情含商品行与金额",
                detail_order.get("code") == 200 and isinstance(vo.get("orderGoodsVOList"), list),
                f"{first_order['orderSn']} 状态={vo.get('orderStatusText')}",
            )
            record(
                "订单列表带 handleOption（按钮由后端决定）",
                "handleOption" in first_order,
                str(first_order.get("handleOption"))[:60],
            )

            # A15/A16：换一个用户读别人的订单必须被拒（走代理路径也一样）
            other = login(client, OTHER_MOBILE)
            intrude = get(client, f"/order/detail/{first_order['orderSn']}", other)
            record(
                "越权读他人订单被拒（A15 在代理路径生效）",
                intrude.status_code == 200 and intrude.json().get("code") != 200,
                f"code={intrude.json().get('code')} msg={intrude.json().get('msg')}",
            )

        # ---------- 9.5 商品评价（上游自带 50 条真实数据） ----------
        if goods_id:
            tags = client.post(
                f"{BASE_URL}/api/mall/comment/tagNum",
                params={"goodsId": goods_id},
                headers={"Authorization": token},
            ).json()
            record(
                "评价统计可用（好评/中评/差评/有图）",
                tags.get("code") == 200
                and {"goodsNum", "middleNum", "badNum", "totalNum"} <= set(tags.get("data") or {}),
                str(tags.get("data")),
            )
            clist = client.post(
                f"{BASE_URL}/api/mall/comment/list",
                params={"goodsId": goods_id, "pageNum": 1, "pageSize": 3},
                headers={"Authorization": token},
            ).json()
            record(
                "评价列表是分页结构（records/total/pages）",
                clist.get("code") == 200 and isinstance((clist.get("data") or {}).get("records"), list),
                f"{(clist.get('data') or {}).get('total')} 条",
            )

        # ---------- 9.6 秒杀 ----------
        sk = client.get(
            f"{BASE_URL}/api/mall/seckill/list",
            params={"pageNum": 1, "pageSize": 5},
            headers={"Authorization": token},
        ).json()
        activities = (sk.get("data") or {}).get("records") or []
        record("秒杀活动列表可用", sk.get("code") == 200, f"{len(activities)} 个活动")
        if activities:
            aid = activities[0]["id"]
            detail = client.get(
                f"{BASE_URL}/api/mall/seckill/detail/{aid}", headers={"Authorization": token}
            ).json()
            skus = (detail.get("data") or {}).get("skuList") or []
            record(
                "秒杀详情带 SKU（含秒杀价与活动库存）",
                detail.get("code") == 200 and bool(skus),
                f"{len(skus)} 个 SKU",
            )
            if skus:
                tk = client.get(
                    f"{BASE_URL}/api/mall/seckill/token/{skus[0]['id']}", headers={"Authorization": token}
                ).json()
                record(
                    "能拿到抢购令牌（秒杀漏斗第一道门）",
                    tk.get("code") == 200 and bool((tk.get("data") or {}).get("token")),
                    f"有效期 {(tk.get('data') or {}).get('expireSecond')}s",
                )

        # ---------- 10. 代理白名单 ----------
        blocked = get(client, "/admin/user/list", token)
        record("非商城路径不被代理", blocked.status_code == 404, f"HTTP {blocked.status_code}")

        # ---------- 清理 ----------
        if cart_id:
            client.delete(f"{BASE_URL}/api/mall/cart/{cart_id}", headers={"Authorization": token})
        for a in mine_addr:
            client.delete(f"{BASE_URL}/api/mall/address/{a['id']}", headers={"Authorization": token})
        print("\n（已清理脚本创建的购物车行与地址）")

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n===== {len(results) - len(failed)}/{len(results)} 通过 =====")
    for name in failed:
        print(f"  FAIL: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 脚本层面炸掉也要给可读信息
        print(f"\n[ERROR] 冒烟脚本异常：{type(exc).__name__}: {exc}")
        sys.exit(1)
