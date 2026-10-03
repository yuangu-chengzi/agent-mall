"""本地工具（不调用 mall）：政策答疑与算术。

- ``faq``：客服政策问答，走**静态条目 + 关键词打分**（决策 D6：不引向量库；RAG 仍属可选项）；
- ``calculator``：给 LLM 一个安全的算术能力（运费/优惠后价格这类口算容易出错）。

两个工具都是**只读、无副作用**的，Agent 可自主调用。
"""

import ast
import operator

from langchain_core.tools import tool

from app.tools.faq_data import FAQ_ENTRIES

_FALLBACK_ANSWER = (
    "这个问题我暂时没有找到对应的政策说明。你可以换个说法再问一次，或者回复「转人工」由人工客服为你解答。"
)


@tool
def faq(question: str) -> str:
    """查询商城的政策与流程说明（退货退款、取消订单、发货时效、物流、优惠券、支付、发票、运费、转人工等）。

    Args:
        question: 用户的问题原文或关键词。
    """
    if not question or not question.strip():
        return _FALLBACK_ANSWER
    text = question.strip()

    # 关键词命中计数打分：命中越多越相关；并列时取靠前的条目（条目的排列顺序就是优先级）
    best_answer, best_score = "", 0
    for _title, keywords, answer in FAQ_ENTRIES:
        score = sum(1 for keyword in keywords if keyword in text)
        if score > best_score:
            best_answer, best_score = answer, score
    return best_answer or _FALLBACK_ANSWER


# --------------------------------------------------------------------------------------
# 计算器：基于 AST 的白名单求值（绝不用 eval）
# --------------------------------------------------------------------------------------
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}
#: 幂运算单独处理，并且限制指数大小，防止 LLM 生成 2**99999999 这类表达式拖垮进程
_POW = ast.Pow
_MAX_POW_EXPONENT = 100


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("只支持数字运算")
        return node.value
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, _POW):
            if abs(right) > _MAX_POW_EXPONENT:
                raise ValueError(f"指数过大（上限 {_MAX_POW_EXPONENT}）")
            return left**right
        func = _BINARY_OPS.get(type(node.op))
        if func is None:
            raise ValueError("只支持 + - * / % ** 和括号")
        return func(left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node, ast.UAdd):
            return +operand
        raise ValueError("只支持正负号")
    raise ValueError("只支持 + - * / % ** 和括号")


@tool
def calculator(expression: str) -> str:
    """计算一个算术表达式（支持 + - * / % ** 与括号），用于算价格、优惠后金额、运费等。

    Args:
        expression: 算术表达式，例如 "39.9 * 2" 或 "(100 - 20) * 0.9"。
    """
    if not expression or not expression.strip():
        return "表达式为空，请提供需要计算的算式。"
    try:
        tree = ast.parse(expression, mode="eval")
        value = _eval_node(tree)
    except ZeroDivisionError:
        return "计算失败：除数为 0。"
    except (SyntaxError, ValueError, TypeError, OverflowError) as exc:
        return f"计算失败：{exc}。只支持数字与 + - * / % ** 和括号。"
    if isinstance(value, float):
        # 抹掉浮点噪声：19.9 * 3 在 IEEE 754 下是 59.699999999999996，直接回给 LLM 容易被读错。
        # 保留 6 位小数对金额场景足够，且能保持"整数就不带小数点"的观感。
        value = round(value, 6)
        if value.is_integer():
            value = int(value)
    return f"{expression} = {value}"
