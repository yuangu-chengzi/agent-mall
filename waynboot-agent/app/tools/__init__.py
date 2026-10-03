"""工具层：Agent 唯一能接触真实世界的手。

**工具清单的演进**（见 docs/PLAN.md）：

- P1：``faq`` / ``calculator`` 两个本地只读工具（不碰 mall），用于跑通图循环；
- P2：接入 mall 的只读工具（search_goods / goods_detail / my_orders /
  order_detail / logistics / my_coupons）；
- P4：写操作工具（request_refund / add_to_cart），必须经 ``interrupt()`` 人工确认。

**约定**：工具一律返回字符串（给 LLM 读），失败时返回**可读的失败说明**而不是抛异常。

为什么不让它抛（实测过，别凭感觉）：LangGraph 的 ``ToolNode`` 默认只把"调用参数错"
（``ToolInvocationError``）转成 ToolMessage，**工具自己抛的业务异常会直接冒泡** →
整个 tools 节点失败 → 这一轮被 runner 兜成通用降级话术，**LLM 连"发生了什么"都看不到，
更没法向用户解释**（实测：异常之后 LLM 根本不会被第二次调用）。
就算改成 ``ToolNode(..., handle_tool_errors=True)`` 让框架兜住，给 LLM 的也只是一句
``Error: RuntimeError('...')``——没有"请让用户重新登录""建议转人工"这类可执行指引。
所以由工具自己返回话术，比让框架兜更可控。
"""

from app.mall.client import MallClient
from app.memory.idempotency import IdempotencyStore
from app.tools.coupons import build_coupon_tools
from app.tools.goods import build_goods_tools
from app.tools.local import calculator, faq
from app.tools.orders import build_order_tools
from app.tools.write import build_write_tools

#: 不依赖 mall 的本地工具（可脱离 HTTP 客户端单独使用，图结构测试用它）
LOCAL_TOOLS = [faq, calculator]


def build_all_tools(client: MallClient, idempotency: IdempotencyStore) -> list:
    """构造交付给 LLM 的完整工具集。

    :param client: mall HTTP 客户端（工具通过闭包持有它，测试时可注入打桩客户端）
    :param idempotency: 写操作的幂等存储（决策 D4）
    """
    return [
        *LOCAL_TOOLS,
        *build_goods_tools(client),
        *build_order_tools(client),
        *build_coupon_tools(client),
        *build_write_tools(client, idempotency),
    ]


__all__ = ["LOCAL_TOOLS", "build_all_tools", "faq", "calculator"]
