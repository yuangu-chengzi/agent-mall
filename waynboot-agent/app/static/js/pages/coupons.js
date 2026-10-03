/**
 * 优惠券页：我的券 + 领券中心。
 *
 * 用到的接口：
 *   GET  /coupon/myList?pageNum&pageSize   我领到的券（records 是 MemberCouponResVO）
 *   GET  /coupon/list?pageNum&pageSize     可领取的券（records 带 receiveStatus）
 *   POST /coupon/receive                  领券 {couponId}
 *
 * 两个接口返回的都是 MyBatis-Plus 的 IPage（`{records, total, size, current, pages}`）。
 * 注：本机 `shop_coupon` 表是空的，所以这里通常显示空态——想看到效果需要先在管理端建券。
 */

import { api } from "../api.js";
import { onLeave } from "../router.js";
import { $, emptyBox, esc, hideLoading, money, navBar, showLoading, skeletonList, toast } from "../ui.js";
import { onReachBottom } from "../widgets.js";

export default async function couponsPage(root) {
  navBar({ title: "优惠券", sub: true });

  root.innerHTML = `
    <div class="tabs" id="tabs">
      <div class="t active" data-tab="mine">我的券</div>
      <div class="t" data-tab="center">领券中心</div>
    </div>
    <div id="box">${skeletonList(2)}</div>
    <div id="sentinel" style="height:1px"></div>
    <div class="hint" id="tip" style="text-align:center"></div>`;

  const box = $("#box", root);
  let tab = "mine";
  let pageNum = 1;

  $("#tabs", root).addEventListener("click", (e) => {
    const t = e.target.closest(".t");
    if (!t || t.dataset.tab === tab) return;
    tab = t.dataset.tab;
    root.querySelectorAll(".t").forEach((el) => el.classList.toggle("active", el === t));
    pageNum = 1;
    load();
  });

  async function load() {
    if (pageNum === 1) box.innerHTML = skeletonList(2);
    try {
      const res =
        tab === "mine"
          ? await api("/coupon/myList", { query: { pageNum, pageSize: 10 } })
          : await api("/coupon/list", { query: { pageNum, pageSize: 10 } });
      const records = res?.records || [];
      if (pageNum === 1) box.innerHTML = records.length ? "" : emptyBox(tab === "mine" ? "还没有优惠券" : "暂时没有可领取的优惠券");
      box.insertAdjacentHTML("beforeend", records.map((c) => card(c, tab)).join(""));
      pageNum += 1;
      const more = Number(res?.pages || 1) >= pageNum;
      $("#tip", root).textContent = more ? "" : records.length ? "— 到底啦 —" : "";
    } catch (e) {
      if (pageNum === 1) box.innerHTML = emptyBox(e.message);
      $("#tip", root).textContent = e.message;
    }
  }

  function card(c, which) {
    const got = which === "mine" || c.receiveStatus === 1;
    const expired = c.expireTime && new Date(c.expireTime) < new Date();
    return `
      <div class="card pad" style="display:flex;align-items:center;gap:14px">
        <div style="flex:none;width:76px;text-align:center;color:var(--price)">
          <div style="font-size:22px;font-weight:600">¥${money(c.discount)}</div>
          <div style="font-size:11px;color:var(--muted)">满 ${money(c.min)} 可用</div>
        </div>
        <div style="flex:1;min-width:0">
          <div style="font-size:14px">${esc(c.title)}</div>
          <div class="hint" style="padding:4px 0 0">
            ${c.expireTime ? `${String(c.expireTime).slice(0, 10)} 到期` : ""}
            ${expired ? " · 已过期" : ""}
          </div>
        </div>
        ${
          which === "mine"
            ? `<span class="tag gray">${c.useStatus === 1 ? "已使用" : expired ? "已过期" : "未使用"}</span>`
            : got
              ? `<span class="tag gray">已领取</span>`
              : `<button class="btn sm primary" data-claim="${c.id}">领取</button>`
        }
      </div>`;
  }

  box.addEventListener("click", async (e) => {
    const id = e.target.closest("[data-claim]")?.dataset.claim;
    if (!id) return;
    showLoading("领取中…");
    try {
      await api("/coupon/receive", { method: "POST", body: { couponId: Number(id) } });
      toast("领取成功，可在「我的券」查看");
      pageNum = 1;
      await load();
    } catch (ex) {
      toast(ex.message);
    } finally {
      hideLoading();
    }
  });

  const stopScroll = onReachBottom($("#sentinel", root), load);
  onLeave(stopScroll);

  await load();
}
