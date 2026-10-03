"""按 mall VO 定义构造的 fixture 载荷。

字段名**逐字**对应 Java 侧 VO（见 docs/API_CONTRACT.md §3，附 file:line 出处），
用途是：在没有运行中的 mall 的情况下，用"结构与字段完全一致"的数据验证工具层的解析逻辑。
⚠️ 这些 fixture 证明的是"工具能正确解析契约规定的字段"，**不能替代真实联调**。
"""

from typing import Any


# --------------------------------------------------------------------------------------
# 商品：/home/recommGoodsList → RecommonGoodsItemResVO
# --------------------------------------------------------------------------------------
def recomm_goods_item(
    goods_id: int = 1024,
    name: str = "小米手机 12",
    retail_price: str = "2999.00",
    **overrides: Any,
) -> dict:
    """首页推荐列表项（无库存字段！）。"""
    item = {
        "id": goods_id,
        "name": name,
        "brief": "旗舰手机 12GB+256GB",
        "picUrl": "https://img.example/1024.png",
        "counterPrice": "3299.00",
        "retailPrice": retail_price,
        "isNew": True,
        "isHot": False,
        "actualSales": 12,
        "virtualSales": 300,
    }
    item.update(overrides)
    return item


# --------------------------------------------------------------------------------------
# 商品：/category/index 与 /category/firstCategoryGoods
# --------------------------------------------------------------------------------------
def category_index_payload(categories: list[dict] | None = None) -> dict:
    """分类首页：categoryList 是 VanTreeSelectVO（id / text / icon）。"""
    cats = (
        categories
        if categories is not None
        else [
            {"id": 1005000, "text": "居家", "icon": ""},
            {"id": 1010000, "text": "服装", "icon": ""},
        ]
    )
    return {
        "currentCategory": {"id": cats[0]["id"], "name": cats[0]["text"]},
        "categoryList": cats,
        "subCategoryList": [],
    }


def category_goods_payload(goods: list[dict], category_id: int = 1005000) -> dict:
    """分类下的商品：goods 是 **Goods 裸实体**（不是首页推荐位 VO），category 是分类实体。"""
    return {"goods": goods, "category": {"id": category_id, "name": "居家"}}


def goods_entity(
    goods_id: int = 1181000,
    name: str = "母亲节礼物-舒适安睡组合",
    retail_price: str = "59.70",
    on_sale: bool = True,
    **overrides: Any,
) -> dict:
    """Goods 实体（分类接口返回的形态）。"""
    entity = {
        "id": goods_id,
        "goodsSn": str(goods_id),
        "name": name,
        "brief": "床垫+枕头组合",
        "picUrl": "https://img.example/1181000.png",
        "retailPrice": retail_price,
        "counterPrice": "79.00",
        "isOnSale": on_sale,
        "isNew": True,
        "isHot": False,
    }
    entity.update(overrides)
    return entity


# --------------------------------------------------------------------------------------
# 商品：/goods/detail/{goodsId} → GoodsDetailResponseVO
# --------------------------------------------------------------------------------------
def goods_detail_payload(
    goods_id: int = 1024,
    name: str = "小米手机 12",
    products: list[dict] | None = None,
    specifications: list[dict] | None = None,
    attributes: list[dict] | None = None,
    **info_overrides: Any,
) -> dict:
    """商品详情四层结构：info(SPU 裸实体) + productList(SKU) + specificationList + attributes。"""
    info = {
        "id": goods_id,
        "goodsSn": str(goods_id),
        "name": name,
        "categoryId": 1001,
        "brandId": 5,
        "gallery": ["https://img.example/1.png", "https://img.example/2.png"],
        "keywords": "手机,小米",
        "brief": "旗舰手机 12GB+256GB",
        "isOnSale": True,
        "picUrl": "https://img.example/1024.png",
        "unit": "台",
        "counterPrice": "3299.00",
        "retailPrice": "2999.00",
        "actualSales": 12,
        "virtualSales": 300,
        # 富文本详情：工具层必须把它裁掉，否则会挤爆上下文
        "detail": "<p>" + "超长商品详情富文本" * 200 + "</p>",
        "delFlag": False,
        "createTime": "2024-01-01 10:00:00",
        "updateTime": "2024-01-02 10:00:00",
    }
    info.update(info_overrides)
    return {
        "info": info,
        "productList": products
        if products is not None
        else [
            goods_product(),
            goods_product(product_id=2049, specs=["白色", "256G"], price="3399.00", number=3),
        ],
        "specificationList": specifications
        if specifications is not None
        else [
            {
                "name": "颜色",
                "valueList": [{"id": 1, "goodsId": goods_id, "specification": "颜色", "value": "白色"}],
            },
            {
                "name": "内存",
                "valueList": [
                    {"id": 2, "goodsId": goods_id, "specification": "内存", "value": "128G"},
                    {"id": 3, "goodsId": goods_id, "specification": "内存", "value": "256G"},
                ],
            },
        ],
        "attributes": attributes
        if attributes is not None
        else [{"id": 9, "goodsId": goods_id, "attribute": "屏幕", "value": "6.36 英寸"}],
    }


def goods_product(
    product_id: int = 2048,
    specs: list[str] | None = None,
    price: str = "2999.00",
    number: int = 12,
    **overrides: Any,
) -> dict:
    """SKU（GoodsProduct）：可售库存看 number。"""
    product = {
        "id": product_id,
        "goodsId": 1024,
        "specifications": specs if specs is not None else ["白色", "128G"],
        "price": price,
        "defaultSelected": True,
        "number": number,
        "lockedStock": 2,
        "version": 3,
        "url": "https://img.example/sku.png",
        "delFlag": False,
    }
    product.update(overrides)
    return product


# --------------------------------------------------------------------------------------
# 订单：/order/list → OrderListResVO（注意双层 data）
# --------------------------------------------------------------------------------------
def order_list_page(items: list[dict], page: int = 1, pages: int = 1) -> dict:
    """订单列表分页体：键名是 data/page/pages（不是 records/current/total）。"""
    return {"data": items, "page": page, "pages": pages}


def order_list_item(
    order_sn: str = "2024010112345678",
    status_text: str = "待发货",
    actual_price: str = "59.70",
    **overrides: Any,
) -> dict:
    """订单列表项：只有状态文案，没有数字状态、没有下单时间。"""
    item = {
        "id": 1001,
        "orderSn": order_sn,
        "actualPrice": actual_price,
        "orderStatusText": status_text,
        "handleOption": {
            "cancel": True,
            "delete": False,
            "pay": False,
            "comment": False,
            "confirm": False,
            "refund": True,
            "rebuy": True,
            "aftersale": False,
        },
        "goodsList": [
            {
                "id": 7,
                "orderId": 1001,
                "goodsId": 1024,
                "goodsName": "小米手机 12",
                "goodsSn": "1024",
                "number": 2,
                "price": "29.85",
                "specifications": ["白色", "128G"],
                "picUrl": "https://img.example/1024.png",
                "comment": 0,
            }
        ],
    }
    item.update(overrides)
    return item


# --------------------------------------------------------------------------------------
# 订单：/order/detail/{orderSn} → OrderDetailVO
# --------------------------------------------------------------------------------------
def order_detail_payload(
    order_sn: str = "2024010112345678",
    order_status: int = 301,
    status_text: str = "已发货",
    shipped: bool = True,
    **overrides: Any,
) -> dict:
    """订单详情：未发货时 shipSn/shipChannel/shipTime 三个 key 会整体缺失（non_null）。"""
    detail = {
        "id": 1001,
        "userId": 7,
        "orderSn": order_sn,
        "orderStatus": order_status,
        "orderStatusText": status_text,
        "consignee": "张三",
        "mobile": "13800000000",
        "address": "江西省南昌市红谷滩区某某路 1 号",
        "goodsPrice": "59.70",
        "freightPrice": "0.00",
        "couponPrice": "0.00",
        "orderPrice": "59.70",
        "actualPrice": "59.70",
        "orderGoodsVOList": order_list_item(order_sn=order_sn)["goodsList"],
    }
    if shipped:
        detail.update(
            {
                "payTime": "2024-01-01 12:30:00",
                "payTypeText": "微信",
                "shipSn": "SF1234567890",
                "shipChannel": "顺丰速运",
                "shipTime": "2024-01-03 10:00:00",
            }
        )
    detail.update(overrides)
    return detail


# --------------------------------------------------------------------------------------
# 优惠券：/coupon/myList → Page<MemberCouponResVO>
# --------------------------------------------------------------------------------------
def coupon_page(records: list[dict], page: int = 1, pages: int = 1, size: int = 20) -> dict:
    """MP Page 序列化后的键：records/total/size/current/pages。"""
    return {"records": records, "total": len(records), "size": size, "current": page, "pages": pages}


def coupon_record(
    title: str = "满 100 减 20",
    discount: int = 20,
    threshold: int = 100,
    use_status: int = 0,
    expire_time: str = "2026-12-31 23:59:59",
    **overrides: Any,
) -> dict:
    """用户券：门槛字段名就叫 min。"""
    record = {
        "id": 1,
        "userId": 7,
        "title": title,
        "couponId": 3,
        "discount": discount,
        "min": threshold,
        "useStatus": use_status,
        "createTime": "2026-01-01 00:00:00",
        "expireTime": expire_time,
    }
    record.update(overrides)
    return record
