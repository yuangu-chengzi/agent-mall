/**
 * 首页：搜索入口 + 轮播 + 金刚区（分类快捷入口）+ 为你推荐（滚动加载）。
 *
 * 用到的接口：
 *   GET /home/index               轮播、金刚区（营销位）
 *   GET /home/recommonGoodsList   推荐商品（分页）
 */

import { api, pic } from "../api.js";
import { onLeave } from "../router.js";
import { refreshCartCount } from "../store.js";
import { $, $$, esc, navBar, navEl, skeletonGrid, toast } from "../ui.js";
import { bindGoodsCards, onReachBottom, pagedGoods } from "../widgets.js";

const SEARCH_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>`;

export default async function homePage(root) {
  navBar({
    title: "wayn 商城",
    sub: false,
    right: `<button class="icon-btn" id="navSearch" aria-label="搜索">${SEARCH_ICON}</button>`,
  });

  root.innerHTML = `
    <div class="searchbar" id="toSearch">${SEARCH_ICON}<span>搜索商品，试试「床垫」</span></div>
    <div id="bannerBox"><div class="banner skeleton" style="margin:0 12px"></div></div>
    <div id="diamondBox"></div>
    <div class="card pad" id="seckillEntry" style="display:flex;align-items:center;gap:10px;cursor:pointer">
      <span class="tag hot">秒杀</span>
      <span style="flex:1;font-size:14px">限时秒杀 · 抢完即止</span>
      <span class="hint" style="padding:0">去看看 ›</span>
    </div>
    <div class="section-title"><span>为你推荐</span></div>
    <div id="goodsBox">${skeletonGrid(4)}</div>
    <div id="sentinel" style="height:1px"></div>
    <div class="hint" id="loadTip" style="text-align:center"></div>`;

  $("#toSearch", root).onclick = () => (location.hash = "#/search");
  $("#seckillEntry", root).onclick = () => (location.hash = "#/seckill");
  navEl("navSearch").onclick = () => (location.hash = "#/search");

  const list = pagedGoods({
    box: $("#goodsBox", root),
    tip: $("#loadTip", root),
    fetchPage: (pageNum, pageSize) => api("/home/recommonGoodsList", { query: { pageNum, pageSize } }),
    emptyText: "暂时没有推荐商品",
  });
  bindGoodsCards($("#goodsBox", root));
  const stopScroll = onReachBottom($("#sentinel", root), () => list.load());
  const stopBanner = await renderIndex(root);

  onLeave(() => {
    stopScroll();
    stopBanner();
  });

  await Promise.all([list.load(), refreshCartCount()]);
}

/** 轮播 + 金刚区；返回清理函数（清掉轮播定时器）。 */
async function renderIndex(root) {
  let data;
  try {
    data = await api("/home/index");
  } catch (e) {
    $("#bannerBox", root).innerHTML = "";
    toast(e.message);
    return () => {};
  }

  const banners = (data.bannerList || []).filter((b) => b.imgUrl);
  const bannerBox = $("#bannerBox", root);
  let stopBanner = () => {};

  if (banners.length) {
    bannerBox.innerHTML = `
      <div class="banner">
        <div id="bannerTrack" style="display:flex;overflow-x:auto;scroll-snap-type:x mandatory">
          ${banners
            .map(
              (b) =>
                `<img src="${esc(pic(b.imgUrl))}" alt="${esc(b.title || "")}" style="width:100%;flex:none;aspect-ratio:16/7;object-fit:cover;scroll-snap-align:center">`,
            )
            .join("")}
        </div>
        <div class="dots">${banners.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div>
      </div>`;

    const track = $("#bannerTrack", root);
    const dots = $$(".dots i", bannerBox);
    track.addEventListener("scroll", () => {
      const idx = Math.round(track.scrollLeft / track.clientWidth);
      dots.forEach((d, i) => d.classList.toggle("on", i === idx));
    });
    if (banners.length > 1) {
      const timer = setInterval(() => {
        const next = (Math.round(track.scrollLeft / track.clientWidth) + 1) % banners.length;
        track.scrollTo({ left: next * track.clientWidth, behavior: "smooth" });
      }, 4500);
      stopBanner = () => clearInterval(timer);
    }
  } else {
    bannerBox.innerHTML = "";
  }

  const diamonds = (data.diamondList || []).slice(0, 10);
  const box = $("#diamondBox", root);
  box.innerHTML = diamonds.length
    ? `<div class="diamonds">${diamonds
        .map(
          (d) => `<div class="diamond" data-cate="${d.jumpType === 1 ? d.valueId : ""}" data-name="${esc(d.name)}">
            ${d.iconUrl ? `<img src="${esc(pic(d.iconUrl))}" alt="">` : `<div style="width:44px;height:44px;margin:0 auto 6px;border-radius:50%;background:var(--bg)"></div>`}
            <span>${esc(d.name)}</span></div>`,
        )
        .join("")}</div>`
    : "";

  box.addEventListener("click", (e) => {
    const el = e.target.closest(".diamond");
    if (!el) return;
    if (el.dataset.cate) location.hash = `#/list?cate=${el.dataset.cate}&name=${encodeURIComponent(el.dataset.name)}`;
    else toast("该入口暂未配置跳转");
  });

  return stopBanner;
}
