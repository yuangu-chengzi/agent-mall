"""商品工具测试（mock HTTP，不依赖 mall 进程）。"""

import httpx

from app.tools.goods import build_goods_tools
from tests.conftest import fail, ok, token_config, tool_by_name
from tests.payloads import category_goods_payload, category_index_payload, goods_detail_payload, goods_entity


def _category_handler(*, goods: list[dict], categories: list[dict] | None = None):
    """按路径分发：分类首页 + 分类下的商品。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/category/index":
            return httpx.Response(200, json=ok(category_index_payload(categories)))
        if path == "/category/firstCategoryGoods":
            return httpx.Response(200, json=ok(category_goods_payload(goods)))
        return httpx.Response(404, text=f"未打桩 {path}")

    return handler


async def test_search_goods_scans_categories_and_matches_by_name(make_client, mall_requests) -> None:
    """按名称子串匹配：命中的给 id 和价格，未命中的不出现；并且是按分类扫描、带上 pageSize。"""
    goods = [
        goods_entity(1024, "小米手机 12", "2999.00"),
        goods_entity(1025, "红米手机 Note", "1299.00"),
        goods_entity(1026, "纯棉 T 恤", "59.00"),
    ]
    async with make_client(_category_handler(goods=goods)) as client:
        search = tool_by_name(build_goods_tools(client), "search_goods")
        result = await search.ainvoke({"keyword": "手机"}, config=token_config())

    assert "id=1024" in result
    assert "id=1025" in result
    assert "id=1026" not in result
    assert "2999.00" in result
    # 两个接口都是匿名的：不该带 Authorization
    assert all("Authorization" not in request.headers for request in mall_requests)
    category_calls = [r for r in mall_requests if r.url.path == "/category/firstCategoryGoods"]
    assert len(category_calls) == 2  # fixture 里有两个一级分类
    assert "pageSize=200" in str(category_calls[0].url)


async def test_search_goods_skips_offshelf_and_dedupes(make_client) -> None:
    """下架商品不参与匹配；同一个商品出现在多个分类里只算一次。"""
    goods = [
        goods_entity(1024, "小米手机 12", on_sale=True),
        goods_entity(1024, "小米手机 12", on_sale=True),  # 重复
        goods_entity(1027, "小米手机 13（下架）", on_sale=False),
    ]
    async with make_client(_category_handler(goods=goods)) as client:
        search = tool_by_name(build_goods_tools(client), "search_goods")
        result = await search.ainvoke({"keyword": "小米手机"}, config=token_config())

    assert "共 1 个" in result  # 去重后只剩一个
    assert "id=1027" not in result  # 下架的被过滤


async def test_search_goods_explains_it_is_not_a_full_search(make_client) -> None:
    """没匹配上时要说清"这是按分类扫描 + 本地匹配"，不假装是全文搜索。"""
    async with make_client(_category_handler(goods=[goods_entity(1026, "纯棉 T 恤", "59.00")])) as client:
        search = tool_by_name(build_goods_tools(client), "search_goods")
        result = await search.ainvoke({"keyword": "手机"}, config=token_config())

    assert "没有找到" in result
    assert "不是全文搜索" in result


async def test_search_goods_asks_for_keyword_without_calling_mall(make_client, mall_requests) -> None:
    """空关键词直接要关键词，不做无意义的 HTTP 调用。"""
    async with make_client() as client:
        search = tool_by_name(build_goods_tools(client), "search_goods")
        result = await search.ainvoke({"keyword": "   "}, config=token_config())

    assert "请先问他想找什么商品" in result
    assert mall_requests == []


async def test_search_goods_degrades_when_mall_unavailable(make_client) -> None:
    async with make_client(lambda request: httpx.Response(503, text="down")) as client:
        search = tool_by_name(build_goods_tools(client), "search_goods")
        result = await search.ainvoke({"keyword": "手机"}, config=token_config())

    assert "暂时不可用" in result


async def test_goods_detail_summarises_sku_stock_and_drops_rich_text(make_client) -> None:
    """关键行为：给出每个 SKU 的价格与可售库存；富文本详情必须被裁掉（上下文经济）。"""
    payload = goods_detail_payload()
    async with make_client(lambda request: httpx.Response(200, json=ok(payload))) as client:
        detail = tool_by_name(build_goods_tools(client), "goods_detail")
        result = await detail.ainvoke({"goodsId": 1024}, config=token_config())

    assert "小米手机 12" in result
    assert "库存 12" in result  # number=12 的 SKU
    assert "库存 3" in result  # number=3 的 SKU
    assert "2999.00" in result
    assert "可选规格" in result
    assert "内存" in result
    # 富文本详情（可能几十 KB）不能进上下文
    assert "超长商品详情富文本" not in result
    assert "<p>" not in result


async def test_goods_detail_reports_missing_goods(make_client) -> None:
    """mall 对不存在的商品仍返回 code=200（只是 info 缺失），工具要判空。"""
    empty = {"productList": [], "specificationList": [], "attributes": []}
    async with make_client(lambda request: httpx.Response(200, json=ok(empty))) as client:
        detail = tool_by_name(build_goods_tools(client), "goods_detail")
        result = await detail.ainvoke({"goodsId": 999}, config=token_config())

    assert "没有找到 id=999" in result


async def test_goods_detail_degrades_on_business_error(make_client) -> None:
    """商城明确拒绝时，把它的话术交回给 LLM（不吞掉原因，也不编造原因）。"""
    async with make_client(lambda request: httpx.Response(200, json=fail(500, "操作失败"))) as client:
        detail = tool_by_name(build_goods_tools(client), "goods_detail")
        result = await detail.ainvoke({"goodsId": 1024}, config=token_config())

    assert "操作失败" in result
    assert "不要编造" in result


async def test_search_goods_without_keyword_asks_instead_of_guessing(make_client, mall_requests) -> None:
    """没给关键词时**不能瞎搜**：直接把"先问用户"的提示交回给 LLM，且一次 mall 请求都不发。

    这条是用户实机反馈后补的：聊天页的「找商品」快捷项原先直接发"帮我找找有没有床垫"
    （模板里写死了商品名），于是用户还没说要找什么，AI 就照着"床垫"答了。
    前端已经改成"只填话头、等用户补"，这里守的是工具侧的底线——
    无论上层怎么传，空关键词都必须走反问，而不是拿目录里的东西凑答案。
    """
    async with make_client() as client:
        search = tool_by_name(build_goods_tools(client), "search_goods")
        result = await search.ainvoke({"keyword": "  "}, config=token_config())

    assert "请先问他想找什么商品" in result
    assert mall_requests == []  # 没关键词就不该去扫目录
