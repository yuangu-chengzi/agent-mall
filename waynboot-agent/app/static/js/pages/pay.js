/**
 * 支付页（`#/pay/订单号`）。
 *
 * 用到的接口：
 *   GET  /order/detail/{orderSn}  读订单（拿应付金额与状态）
 *   POST /pay/prepay              预支付（按 payType 分发到对应渠道策略）
 *
 * 支付渠道说明（见 waynboot-payment-channel）：
 *   1 微信H5 / 2 支付宝H5 / 3 微信JSAPI / 4·5 易支付 —— 都要真实商户参数，本地跑不通；
 *   99 测试支付（wayn.pay.test.enabled=true 时装配的 TestPayStrategy）—— 直接把订单置为已付款，
 *      开发联调就走它。所以这里只放行 99，其余置灰并说明原因，不假装能用。
 */

import { api } from "../api.js";
import { refreshCartCount } from "../store.js";
import { $, emptyBox, esc, money, navBar, skeletonList, toast } from "../ui.js";

const STATUS_UNPAID = 101;
const PAY_TYPE_TEST = 99;

const METHODS = [
  { type: PAY_TYPE_TEST, name: "测试支付", desc: "开发环境专用，直接标记为已付款", enabled: true },
  { type: 1, name: "微信 H5", desc: "未配置商户参数", enabled: false },
  { type: 2, name: "支付宝 H5", desc: "未配置商户参数", enabled: false },
  { type: 4, name: "易支付", desc: "未配置商户参数", enabled: false },
];

export default async function payPage(root, params) {
  const orderSn = params.sn;
  navBar({ title: "收银台", sub: true });

  root.innerHTML = `<div id="payBox">${skeletonList(3)}</div>`;
  const box = $("#payBox", root);

  let order;
  try {
    order = await api(`/order/detail/${orderSn}`);
  } catch (e) {
    box.innerHTML = emptyBox(e.message, "查看我的订单", "#/orders");
    return;
  }

  if (Number(order.orderStatus) !== STATUS_UNPAID) {
    box.innerHTML = `
      <div class="card pad" style="margin-top:26px;text-align:center">
        <div style="font-size:34px;color:var(--ok)">✓</div>
        <h2 style="margin:10px 0 4px;font-size:17px">该订单不需要支付</h2>
        <p class="hint" style="padding:0">当前状态：${esc(order.orderStatusText || order.orderStatus)}</p>
        <div style="margin-top:18px;display:flex;gap:10px;justify-content:center">
          <a class="btn" href="#/orders">我的订单</a>
          <a class="btn primary" href="#/order/${esc(orderSn)}">订单详情</a>
        </div>
      </div>`;
    return;
  }

  let selected = PAY_TYPE_TEST;
  let paying = false;

  render();

  function render() {
    box.innerHTML = `
      <div class="pay-amount">
        <div class="num"><span class="sym">¥</span>${money(order.actualPrice)}</div>
        <div class="tip">订单号 ${esc(order.orderSn)}</div>
      </div>

      <div class="card">
        ${METHODS.map(
          (m) => `
          <div class="pay-option ${selected === m.type ? "on" : ""} ${m.enabled ? "" : "disabled"}" data-type="${m.type}"
               style="${m.enabled ? "" : "opacity:.5;cursor:not-allowed"}">
            <span class="dot"></span>
            <span class="name">${esc(m.name)}${m.enabled ? "" : `<small>${esc(m.desc)}</small>`}</span>
            ${selected === m.type ? `<span class="tag">已选</span>` : ""}
          </div>`,
        ).join("")}
      </div>

      <div class="card pad">
        <div class="hint" style="padding:0">
          真实支付渠道需要商户号/证书，本机没有配置，因此只开放测试支付。
          这也是 docs/PLAN.md 里「支付没有真的调用微信这种留着」的那部分。
        </div>
      </div>

      <div style="padding:16px 12px">
        <button class="btn primary block lg" id="doPay" ${paying ? "disabled" : ""}>
          ${paying ? "支付中…" : `确认支付 ¥${money(order.actualPrice)}`}
        </button>
        <button class="btn block ghost" style="margin-top:8px" id="later">稍后支付</button>
      </div>`;

    $("#doPay", box).onclick = doPay;
    $("#later", box).onclick = () => (location.hash = `#/order/${orderSn}`);
  }

  // 只绑一次（重绘不会叠加监听器），且绑在页面自己的容器上，切页即失效
  box.addEventListener("click", onPick);

  function onPick(e) {
    const opt = e.target.closest(".pay-option");
    if (!opt) return;
    const method = METHODS.find((m) => m.type === Number(opt.dataset.type));
    if (!method.enabled) {
      toast(`${method.name}：${method.desc}`);
      render();
      return;
    }
    selected = method.type;
    render();
  }

  async function doPay() {
    if (paying) return;
    paying = true;
    render();
    try {
      await api("/pay/prepay", { method: "POST", body: { orderSn, payType: selected } });
      // 测试支付是同步改状态，但状态落库要一点时间，轮询确认后再报成功
      const ok = await waitPaid(orderSn);
      if (ok) {
        await refreshCartCount();
        location.hash = `#/order/${orderSn}`;
        toast("支付成功");
      } else {
        toast("支付已提交，但订单状态还没更新，请稍后在订单详情确认");
        location.hash = `#/order/${orderSn}`;
      }
    } catch (e) {
      toast(e.message);
      paying = false;
      render();
    }
  }
}

async function waitPaid(orderSn, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const o = await api(`/order/detail/${orderSn}`);
      if (Number(o.orderStatus) !== STATUS_UNPAID) return true;
    } catch {
      /* 状态查询失败就再试一次 */
    }
    await new Promise((r) => setTimeout(r, 800));
  }
  return false;
}
