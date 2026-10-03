/**
 * 搜索页：热门词 / 联想 / 搜索历史 + 结果列表。
 *
 * 用到的接口：
 *   GET  /search/hotKeywords      热门搜索词（没输关键词时展示）
 *   GET  /search/sugguest?keyword 输入联想
 *   GET  /search/result?keyword   搜索结果（走 ES，见 waynboot-mobile-api 的 SearchApplicationService）
 *   GET  /searchHistory/list      搜索历史（存在 mall 侧，跟账号绑定）
 *   POST /searchHistory           记一条搜索历史
 *   DELETE /searchHistory/all     清空历史
 *
 * 注：`keyword` 必须 encodeURIComponent（api.js 的 buildQuery 已经做了），
 * 直接把中文塞进 URL 会被 Tomcat 判成 400。
 */

import { api } from "../api.js";
import { go, onLeave } from "../router.js";
import { $, esc, navBar, toast } from "../ui.js";
import { bindGoodsCards, onReachBottom, pagedGoods } from "../widgets.js";

const SEARCH_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>`;
const BACK_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>`;

export default async function searchPage(root, params, query) {
  const initial = query.get("kw") || "";

  navBar({
    sub: true,
    raw: `<div class="nav">
      <div class="left"><button class="icon-btn" id="navBack" aria-label="返回">${BACK_ICON}</button></div>
      <div class="searchbar" style="margin:0;flex:1">
        ${SEARCH_ICON}
        <input id="kw" style="border:0;background:transparent;outline:none;flex:1;font-size:14px"
               placeholder="搜索商品" value="${esc(initial)}" enterkeyhint="search">
      </div>
      <div class="right"><button class="btn ghost sm" id="doSearch">搜索</button></div>
    </div>`,
  });

  root.innerHTML = `
    <div id="panel"></div>
    <div id="goodsBox" style="display:none"></div>
    <div id="sentinel" style="height:1px"></div>
    <div class="hint" id="loadTip" style="text-align:center"></div>`;

  // 搜索框在 #nav 里（#view 之外），所以按文档查询
  const kwInput = $("#kw");
  const panel = $("#panel", root);
  const goodsBox = $("#goodsBox", root);

  let currentKeyword = "";
  let suggestTimer = null;

  const list = pagedGoods({
    box: goodsBox,
    tip: $("#loadTip", root),
    fetchPage: (pageNum, pageSize) => api("/search/result", { query: { keyword: currentKeyword, pageNum, pageSize } }),
    emptyText: "没有找到相关商品，换个词试试",
  });
  bindGoodsCards(goodsBox);
  const stopScroll = onReachBottom($("#sentinel", root), () => list.load());
  onLeave(() => {
    stopScroll();
    clearTimeout(suggestTimer);
  });

  function showIdle() {
    goodsBox.style.display = "none";
    panel.style.display = "";
    renderIdle();
  }

  async function renderIdle() {
    panel.innerHTML = `<div class="section-title">热门搜索</div><div id="hot" class="card pad"><div class="skeleton sk-line"></div></div>
      <div class="section-title">搜索历史<a class="more" id="clearHis" style="cursor:pointer">清空</a></div><div id="his"></div>`;

    try {
      const hot = await api("/search/hotKeywords");
      const words = hot?.hotStrings || [];
      $("#hot", panel).innerHTML = words.length
        ? `<div style="display:flex;gap:8px;flex-wrap:wrap">${words
            .map((w) => `<span class="pill" data-kw="${esc(w)}" style="cursor:pointer">${esc(w)}</span>`)
            .join("")}</div>`
        : `<span class="hint" style="padding:0">暂无热门搜索词</span>`;
    } catch {
      $("#hot", panel).innerHTML = `<span class="hint" style="padding:0">热门词加载失败</span>`;
    }

    try {
      const history = await api("/searchHistory/list");
      const items = Array.isArray(history) ? history : [];
      $("#his", panel).innerHTML = items.length
        ? `<div class="card">${items
            .map((h) => `<div class="cell" data-kw="${esc(h.keyword)}"><div class="cell-label">${esc(h.keyword)}</div></div>`)
            .join("")}</div>`
        : `<div class="hint">还没有搜索记录</div>`;
    } catch {
      $("#his", panel).innerHTML = `<div class="hint">搜索历史加载失败</div>`;
    }

    $("#clearHis", panel).onclick = async () => {
      try {
        await api("/searchHistory/all", { method: "DELETE" });
        $("#his", panel).innerHTML = `<div class="hint">还没有搜索记录</div>`;
        toast("已清空搜索历史");
      } catch (e) {
        toast(e.message);
      }
    };
  }

  // 热门词/历史点击 → 直接搜
  panel.addEventListener("click", (e) => {
    const el = e.target.closest("[data-kw]");
    if (!el) return;
    kwInput.value = el.dataset.kw;
    runSearch(el.dataset.kw);
  });

  async function showSuggest(keyword) {
    try {
      const words = await api("/search/sugguest", { query: { keyword } });
      if (!Array.isArray(words) || !words.length) return showIdle();
      goodsBox.style.display = "none";
      panel.style.display = "";
      panel.innerHTML = `<div class="sug-list">${words
        .map((w) => `<div class="sug" data-kw="${esc(w)}">${esc(w)}</div>`)
        .join("")}</div>`;
    } catch {
      /* 联想失败就不打扰用户 */
    }
  }

  kwInput.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    const value = kwInput.value.trim();
    suggestTimer = setTimeout(() => (value ? showSuggest(value) : showIdle()), 250);
  });
  kwInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runSearch(kwInput.value.trim());
    }
  });
  $("#doSearch").onclick = () => runSearch(kwInput.value.trim());

  function runSearch(keyword) {
    if (!keyword) return toast("请输入搜索词");
    kwInput.blur();
    // 记录搜索历史（失败不影响搜索本身）
    api("/searchHistory", { method: "POST", body: { keyword } }).catch(() => {});
    // 换关键词就走路由重渲染：同一个词再点一次也能够重新触发（go 对同哈希会强制重解析）
    go(`#/search?kw=${encodeURIComponent(keyword)}`);
  }

  if (initial) {
    currentKeyword = initial;
    panel.style.display = "none";
    goodsBox.style.display = "";
    await list.load();
  } else {
    await renderIdle();
    kwInput.focus();
  }
}
