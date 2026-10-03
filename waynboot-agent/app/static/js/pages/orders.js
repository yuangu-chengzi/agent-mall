/**
 * 我的订单列表（`#/orders?showType=1`）。
 *
 * 用到的接口：
 *   GET  /order/list?showType=   列表（showType：0全部 1待付款 2待发货 3待收货 4待评价）
 *   POST /order/statusCount      各状态数量（给顶部小标签用）
 *   POST /order/cancel/{id}      取消订单
 *   POST /order/confirm/{id}     确认收货
 *   POST /order/delete/{id}      删除订单
 *   POST /order/refund/{id}      申请退款
 *
 * 两个容易踩的点：
 *   ① 列表返回的是 `{data:[...], pages, page}`——**外面还套了一层信封**，所以是 res.data 而不是 res；
 *   ② 按钮该不该显示由后端给的 `handleOption` 决定（状态机在服务端），前端不要自己猜。
 */

import { api, pic } from "../api.js";
import { onLeave } from "../router.js";
import { $, $$, confirmBox, emptyBox, esc, hideLoading, money, navBar, showLoading, skeletonList, toast } from "../ui.js";
import { onReachBottom } from "../widgets.js";

const TABS = [
  { type: 0, name: "全部" },
  { type: 1, name: "待付款", countKey: "unpaid" },
  { type: 2, name: "待发货", countKey: "unship" },
  { type: 3, name: "待收货", countKey: "unrecv" },
  { type: 4, name: "待评价", countKey: "uncomment" },
];

export default async function ordersPage(root, params, query) {
  const showType = Number(query.get("showType") || 0);

  navBar({ title: "我的订单", sub: true });

  root.innerHTML = `
    <div class="tabs" id="tabs"></div>
    <div id="listBox">${skeletonList(3)}</div>
    <div id="sentinel" style="height:1px"></div>
    <div class="hint" id="tip" style="text-align:center"></div>`;

  let pageNum = 1;
  let totalPages = 1;
  let loading = false;

  renderTabs({});

  // 状态数量是锦上添花，失败就当没有
  api("/order/statusCount", { method: "POST" })
    .then((counts) => renderTabs(counts || {}))
    .catch(() => {});

  function renderTabs(counts) {
    $("#tabs", root).innerHTML = TABS.map(
      (t) =>
        `<div class="t ${t.type === showType ? "active" : ""}" data-type="${t.type}">${esc(t.name)}${
          counts[t.countKey] ? ` (${counts[t.countKey]})` : ""
        }</div>`,
    ).join("");
    $$("#tabs .t", root).forEach((el) => {
      el.onclick = () => (location.hash = `#/orders?showType=${el.dataset.type}`);
    });
  }

  async function loadMore() {
    if (loading || pageNum > totalPages) return;
    loading = true;
    const box = $("#listBox", root);
    try {
      const res = await api("/order/list", { query: { showType, pageNum, pageSize: 5 } });
      const list = res?.data || [];
      totalPages = Number(res?.pages ?? 1) || 1;
      if (pageNum === 1) box.innerHTML = list.length ? "" : emptyBox("这里还没有订单", "去逛逛", "#/home");
      if (list.length) {
        const html = list.map(orderCard).join("");
        if (pageNum === 1) box.innerHTML = html;
        else box.insertAdjacentHTML("beforeend", html);
      }
      pageNum += 1;
      $("#tip", root).textContent = pageNum > totalPages && list.length ? "— 到底啦 —" : "";
    } catch (e) {
      if (pageNum === 1) box.innerHTML = emptyBox(e.message);
      $("#tip", root).textContent = e.message || "";
      totalPages = 0;
    } finally {
      loading = false;
    }
  }

  function orderCard(o) {
    const goods = o.goodsList || [];
    const count = goods.reduce((n, g) => n + Number(g.number || 0), 0);
    const can = o.handleOption || {};
    const statusClass = /未付款/.test(o.orderStatusText || "") ? "warn" : /已付款|已发货|收货/.test(o.orderStatusText || "") ? "ok" : "";

    return `
      <div class="order-card" data-id="${o.id}" data-sn="${esc(o.orderSn)}">
        <div class="top" data-act="detail">
          <span>订单号 ${esc(o.orderSn)}</span>
          <span class="st ${statusClass}">${esc(o.orderStatusText || "")}</span>
        </div>
        ${goods
          .map(
            (g) => `
          <div class="goods-line" data-act="detail">
            <img src="${esc(pic(g.picUrl))}" alt="">
            <div class="info">
              <div class="name">${esc(g.goodsName)}</div>
              ${(g.specifications || []).filter(Boolean).length ? `<div class="spec">${esc(g.specifications.filter(Boolean).join(" / "))}</div>` : ""}
            </div>
            <div style="text-align:right">
              <div style="font-size:13px">¥${money(g.price)}</div>
              <div class="num">×${g.number}</div>
            </div>
          </div>`,
          )
          .join("")}
        <div class="bottom">
          <span class="total">共 ${count} 件 · 实付 <b>¥${money(o.actualPrice)}</b></span>
          ${can.cancel ? `<button class="btn sm" data-act="cancel">取消订单</button>` : ""}
          ${can.pay ? `<button class="btn sm primary" data-act="pay">去支付</button>` : ""}
          ${can.confirm ? `<button class="btn sm" data-act="confirm">确认收货</button>` : ""}
          ${can.comment ? `<button class="btn sm" data-act="comment">评价</button>` : ""}
          ${can.refund ? `<button class="btn sm" data-act="refund">申请退款</button>` : ""}
          ${can.delete ? `<button class="btn sm danger" data-act="delete">删除</button>` : ""}
        </div>
      </div>`;
  }

  $("#listBox", root).addEventListener("click", async (e) => {
    const card = e.target.closest(".order-card");
    if (!card) return;
    const act = e.target.closest("[data-act]")?.dataset.act;
    const orderId = card.dataset.id;
    const orderSn = card.dataset.sn;

    if (!act || act === "detail") {
      location.hash = `#/order/${orderSn}`;
      return;
    }
    if (act === "pay") {
      location.hash = `#/pay/${orderSn}`;
      return;
    }
    if (act === "comment") {
      // 评价要按"订单里的哪个商品"来评，所以跳到详情页里逐行评价
      location.hash = `#/order/${orderSn}`;
      return;
    }
    if (act === "cancel") {
      if (!(await confirmBox({ title: "取消这笔订单？", text: "取消后库存会释放，订单不可恢复", okText: "取消订单", danger: true }))) return;
      await run(() => api(`/order/cancel/${orderId}`, { method: "POST" }), "订单已取消");
      return;
    }
    if (act === "confirm") {
      if (!(await confirmBox({ title: "确认已收到货？", okText: "确认收货" }))) return;
      await run(() => api(`/order/confirm/${orderId}`, { method: "POST" }), "已确认收货");
      return;
    }
    if (act === "refund") {
      if (!(await confirmBox({ title: "申请退款？", text: "退款需平台审核，审核通过后原路退回", okText: "申请退款" }))) return;
      await run(() => api(`/order/refund/${orderId}`, { method: "POST" }), "退款申请已提交");
      return;
    }
    if (act === "delete") {
      if (!(await confirmBox({ title: "删除这笔订单？", okText: "删除", danger: true }))) return;
      await run(() => api(`/order/delete/${orderId}`, { method: "POST" }), "订单已删除");
    }
  });

  /** 统一处理"点一下 → 调接口 → 重新加载列表"。 */
  async function run(fn, successText) {
    showLoading("处理中…");
    try {
      await fn();
      toast(successText);
      pageNum = 1;
      totalPages = 1;
      await loadMore();
    } catch (e) {
      toast(e.message);
    } finally {
      hideLoading();
    }
  }

  const stopScroll = onReachBottom($("#sentinel", root), loadMore);
  onLeave(stopScroll);

  await loadMore();
}
