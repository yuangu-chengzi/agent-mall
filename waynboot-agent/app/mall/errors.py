"""mall 调用异常体系。

分三类的理由是**上层对它们的处理方式完全不同**（见 docs/DECISIONS.md D8 降级策略）：

===================  ==========================================  ==================================
异常                  触发场景                                     上层处理
===================  ==========================================  ==================================
MallAuthError        未登录 / 登录过期（code 401/403）              提示"请先登录"，不重试
MallBusinessError    mall 明确业务拒绝（如 5002 订单不存在）        把 msg 作为话术素材，不重试
MallUnavailableError 网络异常 / 超时 / 非 2xx / 响应不是合法信封    降级话术 + 建议转人工
===================  ==========================================  ==================================
"""


class MallError(Exception):
    """mall 调用失败的基类（便于上层一次性捕获）。"""


class MallBusinessError(MallError):
    """mall 返回了合法信封，但 ``code != 200``：业务拒绝。

    ``msg`` 来自 mall，可直接作为回答用户的素材（例如"订单不存在"）。
    """

    def __init__(self, code: int, msg: str, *, path: str | None = None) -> None:
        self.code = code
        self.msg = msg
        self.path = path
        super().__init__(f"mall 业务错误 code={code} msg={msg} path={path}")

    @property
    def user_message(self) -> str:
        """可直接展示给用户的话术。"""
        return self.msg


class MallAuthError(MallBusinessError):
    """未登录或登录状态已失效。"""

    def __init__(
        self, code: int, msg: str = "登录状态已失效，请重新登录", *, path: str | None = None
    ) -> None:
        super().__init__(code, msg, path=path)

    @property
    def user_message(self) -> str:
        return "请先登录后再查询哦～"


class MallUnavailableError(MallError):
    """拿不到 mall 的合法响应：网络异常、超时、非 2xx、响应体不符合 R 信封。

    注意：本类也覆盖 mall 返回 4xx（例如接口路径写错）。这类失败**不是用户的错**，
    因此对外统一降级为"服务暂时不可用"，并把细节记进日志供排查。
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
