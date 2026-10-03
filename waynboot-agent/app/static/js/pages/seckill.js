/**
 * 秒杀活动列表（`#/seckill`）。
 *
 * 用到的接口：`GET /seckill/list?pageNum=&pageSize=` → IPage<SeckillActivityListItemResVO>
 *
 * 秒杀在下单链路上和普通商品完全不同（活动状态 → 短期 token → 频控 → SKU 限流 →
 * Redis Lua 原子扣减 → 本地消息异步落单），所以前端也是独立页面，见 seckill-detail.js。
 */

import { api } from "../api.js";
import { onLeave } from "../router.js";
import { $, emptyBox, esc, navBar, skeletonList } from "../ui.js";
import { onReachBottom } from "../widgets.js";

export default async function seckillListPage(root) {
  navBar({ title: "限时秒杀", sub: true });

  root.innerHTML = `
    <div id="box">${skeletonList(3)}</div>
    <div id="sentinel" style="height:1px"></div>
    <div class="hint" id="tip" style="text-align:center"></div>`;

  let pageNum = 1;
  let totalPages = 1;
  let loading = false;

  async function load() {
    if (loading || pageNum > totalPages) return;
    loading = true;
    const box = $("#box", root);
    try {
      const res = await api("/seckill/list", { query: { pageNum, pageSize: 10 } });
      const records = res?.records || [];
      totalPages = Number(res?.pages ?? 1) || 1;
      if (pageNum === 1) box.innerHTML = records.length ? "" : emptyBox("暂时没有秒杀活动");
      box.insertAdjacentHTML("beforeend", records.map(card).join(""));
      pageNum += 1;
      $("#tip", root).textContent = pageNum > totalPages && records.length ? "— 到底啦 —" : "";
    } catch (e) {
      if (pageNum === 1) box.innerHTML = emptyBox(e.message);
      $("#tip", root).textContent = e.message || "";
      totalPages = 0;
    } finally {
      loading = false;
    }
  }

  function card(a) {
    const now = Date.now();
    const start = new Date(a.startTime).getTime();
    const end = new Date(a.endTime).getTime();
    // 活动状态：0 草稿 1 已发布 2 已下线（SeckillActivityStatusEnum）；时间再叠加"未开始/已结束"
    const phase = a.status !== 1 ? "已下线" : now < start ? "未开始" : now > end ? "已结束" : "抢购中";
    const on = phase === "抢购中";
    return `
      <div class="card pad" data-id="${a.id}" style="cursor:pointer">
        <div style="display:flex;align-items:center;gap:10px">
          <div style="flex:1;min-width:0">
            <div style="font-size:15px;font-weight:600">${esc(a.name)}</div>
            <div class="hint" style="padding:4px 0 0">${esc(a.brief || "")}</div>
            <div class="hint" style="padding:4px 0 0">${esc(shortTime(a.startTime))} ~ ${esc(shortTime(a.endTime))}</div>
          </div>
          <span class="tag ${on ? "hot" : "gray"}">${esc(phase)}</span>
        </div>
      </div>`;
  }

  function shortTime(value) {
    return String(value || "").replace("T", " ").slice(5, 16);
  }

  $("#box", root).addEventListener("click", (e) => {
    const cardEl = e.target.closest("[data-id]");
    if (cardEl) location.hash = `#/seckill/${cardEl.dataset.id}`;
  });

  const stopScroll = onReachBottom($("#sentinel", root), load);
  onLeave(stopScroll);

  await load();
}
