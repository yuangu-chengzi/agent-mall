/**
 * 登录页：手机号 + 短信验证码。
 *
 * 开发环境 mall 的验证码固定 `1234`（非 prod 分支），登录接口还会自动注册新用户。
 * 走的是 Agent 的登录代理（`/api/login`），浏览器不直连 mall —— 见 docs/DECISIONS.md D2。
 */

import { auth, login, sendLoginCode } from "../api.js";
import { $, esc, navBar, toast } from "../ui.js";

export default async function loginPage(root) {
  navBar({ title: "登录", sub: false });

  root.innerHTML = `
    <div class="card pad" style="margin-top:26px">
      <h2 style="margin:2px 0 6px;font-size:19px">欢迎回来</h2>
      <p class="hint" style="padding:0 0 6px">
        移动端使用短信验证码登录。开发环境验证码固定 <b>1234</b>，任意合法手机号会自动注册。
      </p>
      <div class="field" style="padding:0">
        <label for="mobile">手机号</label>
        <input id="mobile" type="tel" inputmode="numeric" maxlength="11" placeholder="请输入手机号" value="${esc(auth.mobile)}">
      </div>
      <div class="field" style="padding:0">
        <label for="yzm">短信验证码</label>
        <div class="inline">
          <input id="yzm" inputmode="numeric" maxlength="6" placeholder="6 位验证码" value="1234">
          <button class="btn" id="sendCode" style="flex:none">发送验证码</button>
        </div>
      </div>
      <button class="btn primary block lg" id="doLogin" style="margin-top:20px">登录</button>
      <p class="hint" id="err" style="text-align:center;color:var(--danger);min-height:18px"></p>
    </div>`;

  const err = (msg) => {
    $("#err", root).textContent = msg || "";
  };

  $("#sendCode", root).onclick = async (e) => {
    const mobile = $("#mobile", root).value.trim();
    if (!/^1\d{10}$/.test(mobile)) return err("请输入 11 位手机号");
    err("");
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await sendLoginCode(mobile);
      toast("验证码已发送（开发环境固定 1234）");
    } catch (ex) {
      err(ex.message);
    } finally {
      setTimeout(() => (btn.disabled = false), 1000);
    }
  };

  $("#doLogin", root).onclick = async () => {
    const mobile = $("#mobile", root).value.trim();
    const yzm = $("#yzm", root).value.trim();
    if (!/^1\d{10}$/.test(mobile)) return err("请输入 11 位手机号");
    if (!yzm) return err("请输入验证码");
    err("");
    const btn = $("#doLogin", root);
    btn.disabled = true;
    btn.textContent = "登录中…";
    try {
      await login(mobile, yzm);
      // 回到登录前想去的页面（没有就回首页）
      const back = sessionStorage.getItem("mall.redirect") || "#/home";
      sessionStorage.removeItem("mall.redirect");
      location.hash = back === "#/login" ? "#/home" : back;
    } catch (ex) {
      err(ex.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "登录";
    }
  };
}
