"""本地假 mall：给 Agent 做端到端联调用的**契约替身**。

**它不是 mall 的替代品。** 只是一个按 ``docs/API_CONTRACT.md`` 的字段契约返回固定数据的桩服务。
存在的理由：真 mall 需要 MySQL + Redis + ES + RabbitMQ，本机常常起不来；而当我只想验证
"真模型 → 工具 → HTTP → 人工确认 → 写请求"这条链路通不通时，一个桩就够了。

用法::

    .venv/Scripts/python.exe scripts/fake_mall.py          # 默认监听 127.0.0.1:82（mall 的端口）
    # 另一个终端启动 Agent（真模型 + 真 Redis），然后问它"帮我把订单退了"

行为要点（刻意模仿真 mall 的几个"坑"，这些坑正是契约文档里写明的）：

- 业务错误也返回 **HTTP 200** + ``{"code":<非200>}``；
- 写接口的 ``data`` 是 **null**（真 mall 用 ``R.success()`` 无参重载）；
- 需要登录的接口在缺 ``Authorization`` 时返回 ``code=401``；
- ``/order/list`` 的响应是**双层 data**（``data.data``）；
- 申请退款只把订单置为"申请退款中"（202），**不会退钱**。

安全：只监听 ``127.0.0.1``；写接口只把请求记进日志与内存，**不产生任何真实业务后果**。
"""

import json
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

# 复用单测的夹具：桩数据与单测用的是**同一份契约来源**，避免两处各写一遍字段
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.payloads import (  # noqa: E402
    coupon_page,
    coupon_record,
    goods_detail_payload,
    order_detail_payload,
    order_list_item,
    order_list_page,
    recomm_goods_item,
)

ORDER_ID = 1001
ORDER_SN = "2024010112345678"

app = FastAPI(title="fake-mall")

#: 内存状态：被写接口改过的订单 / 购物车（重启即清空）
state: dict = {
    "refunded_orders": set(),
    "cart": {},
    "request_log": [],
}


def ok(data=None) -> dict:
    return {"code": 200, "msg": "操作成功", "data": data}


def fail(code: int, msg: str) -> dict:
    return {"code": code, "msg": msg}


def refundable(order_id: int) -> dict:
    """构造一笔"已付款未发货"的订单（mall 的 handleOption 决定能不能退款）。"""
    options = {
        "cancel": True,
        "delete": False,
        "pay": False,
        "comment": False,
        "confirm": False,
        "refund": order_id not in state["refunded_orders"],
        "rebuy": True,
        "aftersale": False,
    }
    status_text = "申请退款中" if order_id in state["refunded_orders"] else "待发货"
    return order_list_item(order_sn=ORDER_SN, id=ORDER_ID, status_text=status_text, handleOption=options)


def _needs_login(request: Request) -> bool:
    return not request.headers.get("Authorization")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """把所有请求记进内存日志（联调时用来核对 Agent 到底发了什么）。"""
    body = await request.body()
    entry = f"{request.method} {request.url.path}"
    if body:
        entry += f" body={body.decode('utf-8', 'replace')}"
    state["request_log"].append(entry)
    print(f"[fake-mall] {entry}", flush=True)
    return await call_next(request)


@app.get("/home/recommGoodsList")
async def recomm_goods_list(pageNum: int = 1, pageSize: int = 10):
    """首页推荐商品（Agent 的 search_goods 靠它兜底）。"""
    items = [
        recomm_goods_item(1024, "小米手机 12"),
        recomm_goods_item(1025, "红米手机 Note 13"),
        recomm_goods_item(1026, "纯棉短袖 T 恤"),
        recomm_goods_item(1027, "机械键盘 K8"),
    ]
    return ok(items)


@app.get("/goods/detail/{goodsId}")
async def goods_detail(goodsId: int):
    return ok(goods_detail_payload(goods_id=goodsId))


@app.get("/order/list")
async def order_list(request: Request, showType: int = 0, pageNum: int = 1, pageSize: int = 10):
    if _needs_login(request):
        return fail(401, "没有权限，请联系管理员授权")
    items = [] if showType not in (0, 2) else [refundable(ORDER_ID)]
    # 注意双层 data：真 mall 的 OrderListResVO 里还有一个 data
    return ok(order_list_page(items, page=pageNum))


@app.get("/order/detail/{orderSn}")
async def order_detail(request: Request, orderSn: str):
    if _needs_login(request):
        return fail(401, "没有权限，请联系管理员授权")
    if orderSn != ORDER_SN:
        return fail(5002, "订单不存在")
    refunded = ORDER_ID in state["refunded_orders"]
    detail = order_detail_payload(
        order_sn=ORDER_SN,
        order_status=202 if refunded else 201,
        status_text="申请退款中" if refunded else "待发货",
        shipped=False,
    )
    if refunded:
        detail["refundAmount"] = detail["actualPrice"]
    return ok(detail)


@app.get("/coupon/myList")
async def coupon_my_list(request: Request, pageNum: int = 1, pageSize: int = 10):
    if _needs_login(request):
        return fail(401, "没有权限，请联系管理员授权")
    return ok(coupon_page([coupon_record(title="满 100 减 20", discount=20, threshold=100)]))


@app.post("/order/refund/{orderId}")
async def refund(request: Request, orderId: int):
    """申请退款：只把订单标成"申请退款中"，不退钱（与真 mall 行为一致）。"""
    if _needs_login(request):
        return fail(401, "没有权限，请联系管理员授权")
    if orderId != ORDER_ID:
        return fail(5002, "订单不存在")
    if orderId in state["refunded_orders"]:
        return fail(5003, "订单不能退款")
    state["refunded_orders"].add(orderId)
    return ok(None)  # 真 mall 的写接口 data 是 null


@app.post("/cart")
async def add_to_cart(request: Request):
    """加入购物车：重复加购**累加数量**（与真 mall 行为一致）。"""
    if _needs_login(request):
        return fail(401, "没有权限，请联系管理员授权")
    payload = json.loads(await request.body() or b"{}")
    key = f"{payload.get('goodsId')}:{payload.get('productId')}"
    state["cart"][key] = state["cart"].get(key, 0) + int(payload.get("number") or 0)
    return ok(None)


if __name__ == "__main__":
    print("fake-mall 启动：http://127.0.0.1:82（只监听本机；写接口无真实后果）", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=82, log_level="warning")
