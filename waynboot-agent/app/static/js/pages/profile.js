/**
 * 个人中心。
 *
 * 用到的接口：
 *   GET  /user/info         用户信息
 *   POST /order/statusCount 各状态订单数（做徽标）
 *   POST /logout            退出（清 Sa-Token 会话）
 */

import { api, auth, pic } from "../api.js";
import { clearUser, refreshCartCount } from "../store.js";
import { $, confirmBox, esc, maskMobile, navBar, toast } from "../ui.js";

const CHEV = `<svg class="chev" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>`;

export default async function profilePage(root) {
  navBar({ title: "我的", sub: false });

  const cached = auth.user;
  root.innerHTML = `
    <div class="profile-head">
      <img src="${esc(pic(cached?.avatar))}" alt="">
      <div>
        <div class="who">${esc(cached?.nickname || "加载中…")}</div>
        <div class="sub">${esc(maskMobile(cached?.mobile || auth.mobile))}</div>
      </div>
    </div>

    <div class="card">
      <div class="cell" id="allOrders">
        <div class="cell-label">我的订单</div>
        <div class="cell-value">全部订单</div>${CHEV}
      </div>
      <div class="stat-row" id="statRow">
        ${["待付款", "待发货", "待收货", "待评价"].map((n) => `<div class="stat" data-type=""><b>-</b><span>${n}</span></div>`).join("")}
      </div>
    </div>

    <div class="card">
      <div class="cell" data-href="#/profile/edit"><div class="cell-label">编辑资料 / 改密码</div>${CHEV}</div>
      <div class="cell" data-href="#/address"><div class="cell-label">收货地址</div>${CHEV}</div>
      <div class="cell" data-href="#/coupons"><div class="cell-label">我的优惠券</div>${CHEV}</div>
      <div class="cell" data-href="/chat" data-external="1"><div class="cell-label">AI 客服对话</div>${CHEV}</div>
    </div>

    <div class="card">
      <div class="cell" style="cursor:default">
        <div class="cell-label">商城版本</div><div class="cell-value">wayn 1.3.0</div>
      </div>
    </div>

    <div style="padding:18px 12px">
      <button class="btn block danger" id="logout">退出登录</button>
    </div>`;

  // 订单状态入口：数字来自 statusCount，点是跳订单列表对应 tab
  api("/order/statusCount", { method: "POST" })
    .then((counts) => {
      const map = [["unpaid", 1], ["unship", 2], ["unrecv", 3], ["uncomment", 4]];
      $("#statRow", root).innerHTML = map
        .map(
          ([key, type], i) =>
            `<div class="stat" data-type="${type}"><b>${counts?.[key] ?? 0}</b><span>${["待付款", "待发货", "待收货", "待评价"][i]}</span></div>`,
        )
        .join("");
      bindStats();
    })
    .catch(() => bindStats());

  function bindStats() {
    $("#statRow", root)
      .querySelectorAll(".stat")
      .forEach((el) => (el.onclick = () => (location.hash = `#/orders?showType=${el.dataset.type}`)));
  }

  $("#allOrders", root).onclick = () => (location.hash = "#/orders?showType=0");
  root.querySelectorAll("[data-href]").forEach((el) => {
    el.onclick = () => {
      const href = el.dataset.href;
      // 客服页是独立 HTML，用整页跳转（它不在这个单页路由里）
      if (el.dataset.external) location.href = href;
      else location.hash = href;
    };
  });

  $("#logout", root).onclick = async () => {
    if (!(await confirmBox({ title: "退出登录？", okText: "退出" }))) return;
    try {
      await api("/logout", { method: "POST" });
    } catch {
      /* 服务端退出失败也要清本地，否则用户会被卡在登录态 */
    }
    auth.clear();
    clearUser();
    await refreshCartCount();
    toast("已退出登录");
    location.hash = "#/login";
  };

  // 拉最新的用户信息（失败就用缓存里的）
  try {
    const info = await api("/user/info");
    auth.saveUser(info);
    const head = $(".profile-head", root);
    head.querySelector(".who").textContent = info.nickname || "未设置昵称";
    head.querySelector(".sub").textContent = maskMobile(info.mobile);
    if (info.avatar) head.querySelector("img").src = pic(info.avatar);
  } catch {
    /* 保持缓存展示 */
  }
}
