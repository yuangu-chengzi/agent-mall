"""商品类只读工具（匿名接口，不需要 token）。

**商品检索为什么是"本地匹配"**：mall 的 ``/search/result`` 强依赖 Elasticsearch（本机没有）；
而首页接口有两个坑（都是**真机联调时才发现**的，见 docs/API_CONTRACT.md §3.1）：

1. 路径是 ``/home/recommonGoodsList``——作者把 recommend 拼成了 **recommon**，按正确拼写去请求会 500；
2. 它**不接受任何参数**，永远只返回固定的 10 条"推荐位"商品 → 拿它当搜索等于只搜 10 个商品。

所以改成走 **分类接口**：``/category/index`` 拿一级分类，再逐个
``/category/firstCategoryGoods?cateId=X&pageSize=200``（这个接口真的支持 pageSize），
能覆盖整个商品表。ES 就绪后应切回 ``/search/result``。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.mall.client import MallClient
from app.mall.errors import MallError
from app.tools.base import clip, describe_error, money, nz

#: 扫描商品时的分页大小（分类接口实测支持，200 能一次拿完一个大类）
_CATEGORY_PAGE_SIZE = 200
#: 最多扫几个一级分类、最多累计多少个商品（控制请求数与内存）
_MAX_CATEGORIES = 12
_MAX_SCAN_ITEMS = 2000
#: 命中后最多展示几条（控制上下文）
_SEARCH_TOP_N = 5
#: 详情里最多展示几个 SKU / 属性
_DETAIL_TOP_N = 10
_ATTR_TOP_N = 8


async def _scan_on_sale_goods(client: MallClient) -> list[dict]:
    """按一级分类扫一遍在售商品（用于本地名称匹配）。

    去重按商品 id；只保留在售（分类接口的 SQL 本身就带 ``is_on_sale = 1``，这里再兜一层）。
    """
    index = await client.get("/category/index") or {}
    category_ids = [
        category.get("id") for category in (index.get("categoryList") or []) if category.get("id") is not None
    ]

    goods_by_id: dict[int, dict] = {}
    for category_id in category_ids[:_MAX_CATEGORIES]:
        data = await client.get(
            "/category/firstCategoryGoods",
            params={"cateId": category_id, "pageSize": _CATEGORY_PAGE_SIZE},
        )
        for goods in (data or {}).get("goods") or []:
            goods_id = goods.get("id")
            if goods_id is not None and goods.get("isOnSale") is not False:
                goods_by_id[goods_id] = goods
        if len(goods_by_id) >= _MAX_SCAN_ITEMS:
            break
    return list(goods_by_id.values())


def build_goods_tools(client: MallClient) -> list:
    """构造商品工具。闭包持有 client，便于测试注入打桩的客户端。"""

    @tool
    async def search_goods(keyword: str, config: RunnableConfig) -> str:
        """按关键词查找商品（回答「有没有卖 X」「帮我找 XX」这类问题）。

        Args:
            keyword: 商品关键词，尽量短，如「床垫」「连衣裙」。
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return "（系统提示：用户没给关键词，请先问他想找什么商品。）"

        try:
            items = await _scan_on_sale_goods(client)
        except MallError as exc:
            return describe_error(exc)

        matched = [item for item in items if keyword.lower() in str(item.get("name", "")).lower()]
        if not matched:
            return (
                f"在 {len(items)} 个在售商品里没有找到名称含「{keyword}」的。"
                "（说明：这是按分类扫描 + 本地名称匹配，不是全文搜索。可以换个更短/更常见的词再试，"
                "或者建议用户直接在商城搜索框里搜。）"
            )

        lines = [f"在售商品里名称含「{keyword}」的共 {len(matched)} 个，最多列出 {_SEARCH_TOP_N} 个："]
        for item in matched[:_SEARCH_TOP_N]:
            tags = "".join(["、新品" if item.get("isNew") else "", "、热销" if item.get("isHot") else ""])
            lines.append(
                f"- id={item.get('id')} {clip(item.get('name'), 40)}"
                f"｜零售价 {money(item.get('retailPrice'))} 元{tags}"
            )
        lines.append(
            "（提示：这个列表没有库存字段，问「还有没有货」要再用 goods_detail 查商品详情；"
            "最终以下单页为准。）"
        )
        return "\n".join(lines)

    @tool
    async def goods_detail(goodsId: int, config: RunnableConfig) -> str:
        """查询商品详情：价格、规格、每个 SKU 的可售库存、商品参数。

        Args:
            goodsId: 商品 ID（来自 search_goods 的结果，或用户直接提供）。
        """
        try:
            data = await client.get(f"/goods/detail/{goodsId}")
        except MallError as exc:
            return describe_error(exc)

        data = data or {}
        info = data.get("info") or {}
        if not info:
            # mall 对不存在的商品也返回 code=200，只是 info 缺失（见 API_CONTRACT §3.2）
            return f"没有找到 id={goodsId} 的商品，可能已下架或不存在。"

        lines = [
            f"商品：{clip(info.get('name'), 50)}（id={info.get('id')}）",
            f"零售价 {money(info.get('retailPrice'))} 元，专柜价 {money(info.get('counterPrice'))} 元",
            f"是否上架：{'是' if info.get('isOnSale') else '否'}；单位：{nz(info.get('unit'))}",
        ]
        if info.get("brief"):
            lines.append(f"简介：{clip(info.get('brief'), 80)}")

        specs = data.get("specificationList") or []
        if specs:
            spec_text = "；".join(
                f"{s.get('name')}：" + "/".join(str(v.get("value")) for v in (s.get("valueList") or []))
                for s in specs
            )
            lines.append(f"可选规格：{clip(spec_text, 200)}")

        products = data.get("productList") or []
        if products:
            lines.append(f"SKU（id / 规格 / 价格 / 可售库存），共 {len(products)} 个：")
            for product in products[:_DETAIL_TOP_N]:
                spec = "-".join(str(x) for x in (product.get("specifications") or [])) or "默认"
                lines.append(
                    f"- {product.get('id')} / {spec} / {money(product.get('price'))} 元"
                    f" / 库存 {nz(product.get('number'), '未知')}"
                )
            if len(products) > _DETAIL_TOP_N:
                lines.append(f"（只列了前 {_DETAIL_TOP_N} 个）")

        attributes = data.get("attributes") or []
        if attributes:
            attr_text = "；".join(
                f"{a.get('attribute')}={clip(a.get('value'), 20)}" for a in attributes[:_ATTR_TOP_N]
            )
            lines.append(f"商品参数：{attr_text}")

        lines.append(
            "（提示：库存字段 number 就是可售库存，不要再去减 lockedStock；该值可能来自 5 分钟内的缓存。）"
        )
        return "\n".join(lines)

    return [search_goods, goods_detail]
