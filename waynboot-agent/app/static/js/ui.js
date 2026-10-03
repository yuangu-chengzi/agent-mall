/**
 * ui.js —— 渲染工具。页面里只做三件事：拼 HTML、绑事件、调 api。
 * 所有插值都要过 `esc()`，否则商品名里的 `<` 会把页面弄坏（也防 XSS）。
 */

import { pic } from "./api.js";

// 顶部返回箭头的行为由路由决定（路由持有访问栈）。
// 这里用注册的方式拿，避免 ui ⇄ router 互相 import 形成循环。
let backHandler = () => (location.hash = "#/home");
export function setBackHandler(fn) {
  backHandler = fn;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** HTML 转义：任何来自接口的文本都要先过这里。 */
export function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 金额：保留两位小数（后端可能给 999 或 999.0 或 null）。 */
export function money(value) {
  const n = Number(value || 0);
  return n.toFixed(2);
}

/** 带 ¥ 符号的价格 HTML。 */
export function priceHtml(value, cls = "price") {
  return `<span class="${cls}"><span class="sym">¥</span>${money(value)}</span>`;
}

export function imgHtml(url, cls = "") {
  return `<img class="${cls}" src="${esc(pic(url))}" alt="" loading="lazy" onerror="this.src='${PLACEHOLDER_URL}'">`;
}
const PLACEHOLDER_URL =
  "data:image/svg+xml," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#f2f3f5"/><text x="60" y="66" font-size="13" fill="#c2c6cc" text-anchor="middle">暂无图片</text></svg>`,
  );

/** 手机号打码：13800000001 → 138****0001 */
export function maskMobile(mobile) {
  const s = String(mobile || "");
  return s.length === 11 ? `${s.slice(0, 3)}****${s.slice(7)}` : s;
}

/** 订单号/长数字缩写显示。 */
export function shortSn(sn) {
  const s = String(sn || "");
  return s.length > 14 ? `${s.slice(0, 8)}…${s.slice(-4)}` : s;
}

/* ------------------------------ 反馈 ------------------------------ */

let toastTimer = null;
export function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), 2200);
}

export function showLoading(text = "加载中…") {
  const box = $("#loading");
  box.innerHTML = `<div class="box"><span class="spinner"></span>${esc(text)}</div>`;
  box.classList.add("show");
}

export function hideLoading() {
  $("#loading").classList.remove("show");
}

/** 通用底部弹层；返回遮罩元素，调用方自己 close()。 */
export function sheet({ title, body, footer = "" }) {
  const mask = document.createElement("div");
  mask.className = "mask";
  mask.innerHTML = `
    <div class="sheet">
      <div class="sheet-head"><h3>${esc(title)}</h3>
        <button class="icon-btn" data-close>${ICON.close}</button>
      </div>
      <div class="sheet-body">${body}</div>
      ${footer ? `<div class="sheet-foot">${footer}</div>` : ""}
    </div>`;
  const close = () => mask.remove();
  mask.addEventListener("click", (e) => {
    if (e.target === mask || e.target.closest("[data-close]")) close();
  });
  document.body.appendChild(mask);
  return { el: mask, close };
}

/** 确认对话框（Promise<boolean>）。 */
export function confirmBox({ title, text = "", okText = "确定", danger = false }) {
  return new Promise((resolve) => {
    const mask = document.createElement("div");
    mask.className = "mask";
    mask.innerHTML = `
      <div class="dialog">
        <h3>${esc(title)}</h3>
        ${text ? `<p>${esc(text)}</p>` : ""}
        <div class="acts">
          <button class="btn block" data-no>取消</button>
          <button class="btn block ${danger ? "danger" : "primary"}" data-ok>${esc(okText)}</button>
        </div>
      </div>`;
    const done = (value) => {
      mask.remove();
      resolve(value);
    };
    mask.addEventListener("click", (e) => {
      if (e.target === mask || e.target.closest("[data-no]")) done(false);
      if (e.target.closest("[data-ok]")) done(true);
    });
    document.body.appendChild(mask);
  });
}

/* ------------------------------ 占位块 ------------------------------ */

export function emptyBox(text, actionText = "", actionHref = "") {
  return `<div class="empty">
    <div class="big">◌</div>
    <p>${esc(text)}</p>
    ${actionText ? `<a class="btn primary" href="${esc(actionHref)}">${esc(actionText)}</a>` : ""}
  </div>`;
}

export function skeletonGrid(count = 6) {
  return `<div class="grid">${Array.from({ length: count })
    .map(
      () =>
        `<div class="sk-card"><div class="skeleton sk-thumb"></div><div class="skeleton sk-line"></div><div class="skeleton sk-line" style="width:50%"></div></div>`,
    )
    .join("")}</div>`;
}

export function skeletonList(rows = 4) {
  return `<div class="card">${Array.from({ length: rows })
    .map(() => `<div class="cell"><div class="skeleton sk-line" style="flex:1;height:14px"></div></div>`)
    .join("")}</div>`;
}

/* ------------------------------ 图标（内联 SVG，无依赖） ------------------------------ */

export const ICON = {
  back: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>`,
  close: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>`,
  search: `<svg viewBox="0 0 24 24" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>`,
  chev: `<svg class="chev" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>`,
  home: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10.5L12 4l8 6.5V20a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/></svg>`,
  grid: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/></svg>`,
  cart: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h2l2.2 10.5a2 2 0 0 0 2 1.5h7.6a2 2 0 0 0 2-1.6L20 8H6"/><circle cx="10" cy="20" r="1.3"/><circle cx="17.5" cy="20" r="1.3"/></svg>`,
  user: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20c1.2-3.6 4-5.4 7.5-5.4S18.3 16.4 19.5 20"/></svg>`,
  service: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12a7 7 0 0 1 14 0"/><rect x="3" y="12" width="4" height="6" rx="2"/><rect x="17" y="12" width="4" height="6" rx="2"/><path d="M19 18v1a2 2 0 0 1-2 2h-3"/></svg>`,
  plus: `<svg viewBox="0 0 24 24" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg>`,
  location: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>`,
  check: `<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4.5 4.5L19 7"/></svg>`,
};

/* ------------------------------ 顶部导航 ------------------------------ */

/**
 * 渲染顶部导航。sub=true 时（子页面）显示返回箭头 —— 用 history.back()，
 * 没有上一页时回首页，避免从外部直接打开子页面时按返回没反应。
 */
export function navBar({ title = "", sub = false, right = "", raw = "" } = {}) {
  const slot = $("#nav");
  if (raw) {
    // 少数页面（比如搜索）需要在导航栏里放输入框，直接给整条导航的 HTML
    slot.innerHTML = raw;
    if (sub) {
      const btn = $("#navBack", slot);
      if (btn) btn.onclick = () => backHandler();
    }
    return;
  }
  slot.innerHTML = `
    <div class="nav">
      <div class="left">${
        sub
          ? `<button class="icon-btn" id="navBack" aria-label="返回">${ICON.back}</button>`
          : `<span style="width:34px"></span>`
      }</div>
      <div class="title">${esc(title)}</div>
      <div class="right">${right}</div>
    </div>`;
  if (sub) {
    $("#navBack").onclick = () => backHandler();
  }
}

/** 导航栏右侧的按钮 HTML（配合 navBar 的 right 参数）。 */
export function navButton(text, id) {
  return `<button class="btn ghost sm" id="${esc(id)}">${esc(text)}</button>`;
}

/**
 * 取导航栏里的元素。
 *
 * ⚠️ 导航栏（`#nav`）在 `#view` **外面**，而页面渲染函数拿到的 `root` 是 `#view`：
 * 用 `$("#x", root)` 去找导航栏里的按钮会得到 null，赋值时就报
 * `Cannot set properties of null`。这条踩过（首页的搜索图标与地址页的「新增」），
 * 所以这里显式抛一个能自解释的错误，而不是让调用方拿到 null。
 */
export function navEl(id) {
  const el = $(`#nav #${id}`);
  if (!el) throw new Error(`导航栏里没有 #${id}（检查 navBar 的 right 参数里有没有它）`);
  return el;
}
