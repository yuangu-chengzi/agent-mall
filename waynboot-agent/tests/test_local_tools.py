"""本地工具测试：政策答疑与安全算术。"""

from app.tools.local import calculator, faq


def test_faq_matches_policy_keywords() -> None:
    """命中有政策条目的问题要返回政策原文，而不是兜底话术。"""
    answer = faq.invoke({"question": "买错了想退货怎么办"})

    assert "取消" in answer or "退款" in answer
    assert "转人工" not in answer


def test_faq_falls_back_and_points_to_human() -> None:
    """没命中时明确说不知道，并给出转人工出口（不编造政策）。"""
    answer = faq.invoke({"question": "你们老板是谁"})

    assert "没有找到" in answer
    assert "转人工" in answer


def test_faq_handles_blank_question() -> None:
    assert "转人工" in faq.invoke({"question": "   "})


def test_calculator_evaluates_arithmetic() -> None:
    assert calculator.invoke({"expression": "39.9 * 2"}) == "39.9 * 2 = 79.8"


def test_calculator_hides_float_noise() -> None:
    """金额计算不能把 IEEE 754 的尾巴（59.699999999999996）丢给 LLM 读。"""
    assert calculator.invoke({"expression": "19.9 * 3"}) == "19.9 * 3 = 59.7"


def test_calculator_keeps_integer_results_clean() -> None:
    """整数结果不要显示成 160.0。"""
    assert calculator.invoke({"expression": "(100 - 20) * 2"}) == "(100 - 20) * 2 = 160"


def test_calculator_supports_parentheses_and_modulo() -> None:
    assert calculator.invoke({"expression": "(12 + 8) % 7"}) == "(12 + 8) % 7 = 6"


def test_calculator_rejects_arbitrary_code() -> None:
    """安全底线：绝不 eval，函数调用/属性访问一律拒绝。"""
    result = calculator.invoke({"expression": "__import__('os').system('whoami')"})

    assert result.startswith("计算失败")
    assert "只支持" in result


def test_calculator_rejects_oversized_exponent() -> None:
    """防止 LLM 生成 2**99999999 把进程算死。"""
    result = calculator.invoke({"expression": "2 ** 999999"})

    assert "指数过大" in result


def test_calculator_reports_division_by_zero() -> None:
    assert "除数为 0" in calculator.invoke({"expression": "1 / 0"})


def test_calculator_reports_syntax_error() -> None:
    assert "计算失败" in calculator.invoke({"expression": "1 +* 2"})


def test_calculator_handles_blank_expression() -> None:
    assert "表达式为空" in calculator.invoke({"expression": "  "})
