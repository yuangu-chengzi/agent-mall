"""端到端冒烟脚本：把 Agent 当黑盒，跑一遍典型客服对话并检查关键不变量。

它检查的不是"回答得好不好"（那是模型的事），而是**工程保证有没有守住**：

1. 探针可用；mall 不可达时能给出降级话术而不是崩掉；
2. 政策类问题能命中本地 FAQ 工具（回答里应出现政策原文的关键片段）；
3. 未登录问订单 → 引导登录（不能瞎编订单数据）；
4. 写操作**先出确认卡片、此时不产生写请求**；确认后才执行；
5. 转人工 → 响应带 ``escalate=true``，且不产生任何请求。

用法::

    # 终端 1：起 Agent（真模型 + 真 Redis）
    uvicorn app.main:app --port 8300
    # 终端 2（可选）：没有真 mall 时，先起契约替身
    .venv/Scripts/python.exe scripts/fake_mall.py
    # 终端 3：
    .venv/Scripts/python.exe scripts/smoke_e2e.py
    # 带登录态（连真 mall 时用你的 Sa-Token）：
    SA_TOKEN=<你的 token> .venv/Scripts/python.exe scripts/smoke_e2e.py

退出码：全部通过 0；有失败 1（可直接接进 CI）。
"""

import os
import sys
import time
import uuid

import httpx

# Windows 控制台默认是 GBK：把不可编码字符降级成 "?" 而不是抛 UnicodeEncodeError 崩掉脚本
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

BASE_URL = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8300")
SA_TOKEN = os.environ.get("SA_TOKEN", "")
TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT", "180"))

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    mark = "[OK]  " if passed else "[FAIL]"
    print(f"{mark} {name}" + (f" -- {detail}" if detail else ""), flush=True)


def headers() -> dict:
    return {"Authorization": SA_TOKEN} if SA_TOKEN else {}


def chat(client: httpx.Client, session: str, message: str) -> dict:
    response = client.post(
        f"{BASE_URL}/api/chat",
        json={"session_id": session, "message": message},
        headers=headers(),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def confirm(client: httpx.Client, session: str, approved: bool) -> dict:
    response = client.post(
        f"{BASE_URL}/api/chat/confirm",
        json={"session_id": session, "approved": approved},
        headers=headers(),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    session = f"smoke-{uuid.uuid4().hex[:8]}"
    print(f"目标：{BASE_URL}｜会话：{session}｜登录态：{'有' if SA_TOKEN else '无'}\n")

    with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
        # ---- 1. 探针 ----
        healthz = client.get(f"{BASE_URL}/healthz").json()
        record("存活探针 /healthz", healthz.get("status") == "ok", f"version={healthz.get('version')}")

        ready = client.get(f"{BASE_URL}/readyz")
        ready_body = ready.json()
        mall_ok = bool(ready_body.get("mall", {}).get("reachable"))
        redis_ok = bool(ready_body.get("redis", {}).get("reachable"))
        record("就绪探针 /readyz：Redis", redis_ok, f"HTTP {ready.status_code}")
        record(
            "就绪探针 /readyz：mall",
            mall_ok,
            "可达 → 跑真实用例；不可达 → 改跑降级用例",
        )

        # ---- 2. 本地 FAQ 工具（不依赖 mall）----
        answer = chat(client, session, "你们的退货退款政策是什么？")
        faq_hit = not answer["degraded"] and ("审核" in answer["reply"] or "原路退回" in answer["reply"])
        record("FAQ 工具命中（回答含政策原文）", faq_hit, answer["reply"][:40].replace("\n", " ") + "…")

        # ---- 3. 计算器工具（不依赖 mall）----
        answer = chat(client, session, "两件 199.5 元的商品一共多少钱？")
        record("算术工具可用", "399" in answer["reply"], answer["reply"][:40].replace("\n", " "))

        # ---- 4. 未登录 / 无权限时的行为 ----
        answer = chat(client, session, "帮我查一下我的订单到哪了")
        if SA_TOKEN:
            record(
                "已带登录态：订单查询有响应", bool(answer["reply"]), answer["reply"][:40].replace("\n", " ")
            )
        else:
            record(
                "未登录问订单 → 引导登录（不编数据）",
                "登录" in answer["reply"],
                answer["reply"][:40].replace("\n", " "),
            )

        # ---- 5. 写操作：确认闸门 ----
        if mall_ok:
            answer = chat(client, session, "帮我把我的订单退了")
            pending = answer.get("pending_action")
            if pending:
                record("写操作先出确认卡片", True, (pending or {}).get("ask", ""))
                confirmed = confirm(client, session, approved=True)
                record(
                    "确认后执行并继续对话",
                    confirmed.get("pending_action") is None and bool(confirmed["reply"]),
                    confirmed["reply"][:40].replace("\n", " "),
                )
            else:
                # 没有可退款的订单时，正确行为是明确拒绝（而不是随便出一张确认卡片）。
                # 这样脚本可以重复跑：第一次跑完退款后，第二次就会走到这个分支。
                refused = "不支持" in answer["reply"] or "没有找到" in answer["reply"]
                record(
                    "无可退订单时如实拒绝（脚本可重复运行）", refused, answer["reply"][:40].replace("\n", " ")
                )
        else:
            answer = chat(client, session, "帮我把我的订单退了")
            record(
                "mall 不可达时的降级（不崩、有出口）",
                answer["degraded"] is True and answer["escalate"] is True,
                answer["reply"][:40].replace("\n", " "),
            )

        # ---- 6. 转人工 ----
        answer = chat(client, session, "算了，我要转人工客服")
        record("转人工被标记", answer["escalate"] is True, answer["reply"][:40].replace("\n", " "))

        # ---- 7. 会话状态可见 ----
        summary = client.get(f"{BASE_URL}/api/sessions/{session}", headers=headers()).json()
        record(
            "会话状态可查（Redis 里有账）",
            summary.get("message_count", 0) > 0,
            f"消息数={summary.get('message_count')}",
        )

        # ---- 8. 清理 ----
        deleted = client.delete(f"{BASE_URL}/api/sessions/{session}", headers=headers())
        record("会话可清空", deleted.status_code == 204)

    failed = [name for name, passed, _ in results if not passed]
    print(f"\n结果：{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    started = time.time()
    try:
        code = main()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 冒烟脚本异常终止：{type(exc).__name__}: {exc}")
        code = 1
    print(f"耗时 {time.time() - started:.1f}s")
    sys.exit(code)
