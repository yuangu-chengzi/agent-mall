/**
 * 结算页：收货地址 + 商品清单 + 优惠券 + 备注 → 提交订单。
 *
 * 用到的接口：
 *   GET  /address/list             地址列表
 *   POST /cart/getCheckedGoods     已勾选商品汇总（含可用券、运费、应付总额）
 *   POST /order/submit             提交订单（**异步**）
 *   GET  /order/searchResult/{sn}  查询异步下单结果
 *
 * ⚠️ 下单是异步的：`/order/submit` 只是把消息投进 outbox（本地消息表 → relay → MQ → 消费端落库），
 * 接口**立刻**返回订单号，此时订单还没进数据库。所以必须轮询 `searchResult`。
 * 又因为"消费端还没写结果"和"真的失败了"在上游都表现为同一条错误文案，
 * 这里用倒计时（GRACE_MS）兜住：宽限期内一律当"处理中"，超时才报失败。
 */

import { api, pic } from "../api.js";
import { refreshCartCount } from "../store.js";
import { $, confirmBox, emptyBox, esc, hideLoading, money, navBar, showLoading, toast } from "../ui.js";

const GRACE_MS = 15000;
const POLL_MS = 1200;

export default async function checkoutPage(root) {
  navBar({ title: "确认订单", sub: true });

  root.innerHTML = `<div id="body"><div class="card pad"><div class="skeleton sk-line" style="height:16px"></div></div></div>`;

  let addresses = [];
  let summary = null;
  let addressId = null;
  let couponId = null;
  let message = "";
  let submitting = false;

  try {
    [addresses, summary] = await Promise.all([api("/address/list"), api("/cart/getCheckedGoods", { method: "POST" })]);
  } catch (e) {
    $("#body", root).innerHTML = emptyBox(e.message, "回到购物车", "#/cart");
    return;
  }

  const goods = summary?.data || [];
  if (!goods.length) {
    $("#body", root).innerHTML = emptyBox("没有已勾选的商品，先去购物车选几件", "去购物车", "#/cart");
    return;
  }

  const coupons = summary.couponList || [];
  addressId = (addresses.find((a) => a.isDefault) || addresses[0])?.id ?? null;

  render();

  /**
   * 当前应付金额。
   *
   * ⚠️ `orderTotalAmount` 只等于「商品金额 + 运费」，**不含优惠券**——券是提交订单时
   * 才按 `userCouponId` 减掉的（见 CartReadSupport.getCheckedGoods / OrderSubmitSupport）。
   * 所以页面上的"应付"必须自己减一次，否则选了券还显示原价，跟实际扣款对不上。
   */
  function currentPayable() {
    const coupon = coupons.find((c) => c.id === couponId);
    return Math.max(0, Number(summary.orderTotalAmount || 0) - (coupon ? Number(coupon.discount) : 0));
  }

  function render() {
    const address = addresses.find((a) => a.id === addressId);
    const coupon = coupons.find((c) => c.id === couponId);
    const payable = currentPayable();

    $("#body", root).innerHTML = `
      ${
        address
          ? `<div class="card">
              <div class="address-card" id="pickAddress">
                <div style="flex:1">
                  <div class="who">${esc(address.name)}<span>${esc(address.tel)}</span></div>
                  <div class="where">${esc([address.province, address.city, address.county, address.addressDetail].filter(Boolean).join(" "))}</div>
                </div>
                <svg class="chev" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" style="margin-top:6px"><path d="M9 5l7 7-7 7"/></svg>
              </div>
            </div>`
          : `<div class="card">
              <div class="cell" id="pickAddress">
                <div class="cell-label" style="color:var(--brand)">＋ 添加收货地址</div>
                <svg class="chev" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
              </div>
            </div>`
      }

      <div class="card">
        ${goods
          .map(
            (g) => `
          <div class="goods-line">
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
      </div>

      <div class="card">
        <div class="cell" id="pickCoupon">
          <div class="cell-label">优惠券</div>
          <div class="cell-value">${coupon ? esc(coupon.title) : coupons.length ? `可用 ${coupons.length} 张` : "暂无可用"}</div>
          <svg class="chev" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
        </div>
        <div class="cell" style="border-bottom:0">
          <div class="cell-label" style="flex:none">备注</div>
          <input id="msg" placeholder="选填，如收货时间要求" value="${esc(message)}"
                 style="flex:1;border:0;outline:none;text-align:right;background:transparent">
        </div>
      </div>

      <div class="card pad">
        <div class="money-row"><span>商品金额</span><span>¥${money(summary.goodsAmount)}</span></div>
        <div class="money-row"><span>运费</span><span>¥${money(summary.freightPrice)}</span></div>
        ${coupon ? `<div class="money-row"><span>优惠券</span><span style="color:var(--price)">-¥${money(coupon.discount)}</span></div>` : ""}
        <div class="money-row total"><span>应付</span><b>¥${money(payable)}</b></div>
        <p class="hint" style="padding:8px 0 0">提交后订单由消息队列异步落库，可能要等一两秒。</p>
      </div>

      <div style="height:70px"></div>
      <div class="detail-bar">
        <div style="flex:1;font-size:13px;color:var(--text-2)">应付 <b style="color:var(--price);font-size:18px">¥${money(payable)}</b></div>
        <button class="btn primary lg" id="submit" style="flex:none;padding:0 26px" ${submitting ? "disabled" : ""}>提交订单</button>
      </div>`;

    $("#pickAddress", root).onclick = pickAddress;
    $("#pickCoupon", root).onclick = pickCoupon;
    $("#msg", root).oninput = (e) => (message = e.target.value);
    $("#submit", root).onclick = submit;
  }

  function pickAddress() {
    if (!addresses.length) return (location.hash = "#/address?from=checkout");
    const html = addresses
      .map(
        (a) => `<div class="cell" data-id="${a.id}">
          <div class="cell-label">
            <div style="font-size:14px">${esc(a.name)} <span style="color:var(--muted);font-size:12px">${esc(a.tel)}</span>
              ${a.isDefault ? '<span class="tag" style="margin-left:6px">默认</span>' : ""}</div>
            <div style="font-size:12px;color:var(--muted);margin-top:2px">${esc([a.province, a.city, a.county, a.addressDetail].filter(Boolean).join(" "))}</div>
          </div>
        </div>`,
      )
      .join("");
    openSheet("选择收货地址", html, (id) => {
      addressId = Number(id);
      render();
    });
  }

  function pickCoupon() {
    if (!coupons.length) return toast("暂无可用优惠券");
    const html = `
      <div class="cell" data-id=""><div class="cell-label">不使用优惠券</div></div>
      ${coupons
        .map(
          (c) => `<div class="cell" data-id="${c.id}">
            <div class="cell-label">
              <div style="font-size:14px">${esc(c.title)}</div>
              <div class="hint" style="padding:2px 0 0">满 ${money(c.min)} 可用${c.expireTime ? ` · ${String(c.expireTime).slice(0, 10)} 到期` : ""}</div>
            </div>
            <span class="price">-¥${money(c.discount)}</span>
          </div>`,
        )
        .join("")}`;
    openSheet("选择优惠券", html, (id) => {
      couponId = id ? Number(id) : null;
      render();
    });
  }

  /** 用一个临时底部弹层选值，选完即关。 */
  function openSheet(title, bodyHtml, onPick) {
    const mask = document.createElement("div");
    mask.className = "mask";
    mask.innerHTML = `<div class="sheet"><div class="sheet-head"><h3>${esc(title)}</h3></div><div class="sheet-body" style="padding:0">${bodyHtml}</div></div>`;
    mask.addEventListener("click", (e) => {
      if (e.target === mask) return mask.remove();
      const cell = e.target.closest("[data-id]");
      if (!cell) return;
      onPick(cell.dataset.id);
      mask.remove();
    });
    document.body.appendChild(mask);
  }

  async function submit() {
    if (!addressId) return toast("请先添加收货地址");
    if (submitting) return;
    if (!(await confirmBox({ title: "确认提交订单？", text: `应付 ¥${money(currentPayable())}`, okText: "提交" }))) return;

    submitting = true;
    render();
    showLoading("正在提交订单…");
    let orderSn = null;
    try {
      const res = await api("/order/submit", {
        method: "POST",
        body: {
          cartIdArr: goods.map((g) => g.id),
          addressId,
          userCouponId: couponId,
          message,
        },
      });
      orderSn = res?.orderSn;
    } catch (e) {
      hideLoading();
      submitting = false;
      render();
      return toast(e.message);
    }

    // 轮询异步下单结果
    const deadline = Date.now() + GRACE_MS;
    let lastError = "下单失败，请稍后重试";
    while (Date.now() < deadline) {
      try {
        await api(`/order/searchResult/${orderSn}`);
        hideLoading();
        await refreshCartCount();
        toast("下单成功");
        location.hash = `#/pay/${orderSn}`;
        return;
      } catch (e) {
        lastError = e.message || lastError;
        await sleep(POLL_MS);
      }
    }

    hideLoading();
    submitting = false;
    render();
    // 超过宽限期仍然失败：把订单号给用户，客服/后台可以按单号查
    toast(`${lastError}（订单号 ${orderSn}）`);
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
