/**
 * store.js —— 跨页面共享的一点点状态：购物车角标、用户信息。
 * 只放"多个页面都要用、又不必每次都请求"的东西，其余一律页面自己拉。
 */

import { api, auth } from "./api.js";
import { $ } from "./ui.js";

let cartCount = 0;

export function getCartCount() {
  return cartCount;
}

/** 重新统计购物车件数并刷新底部角标。失败不打扰用户（角标只是提示）。 */
export async function refreshCartCount() {
  if (!auth.logged) {
    cartCount = 0;
  } else {
    try {
      cartCount = Number(await api("/cart/goodsCount")) || 0;
    } catch {
      /* 角标失败静默 */
    }
  }
  renderBadge();
  return cartCount;
}

function renderBadge() {
  const tab = $("#tab-cart");
  if (!tab) return;
  const old = $(".badge", tab);
  if (old) old.remove();
  if (cartCount > 0) {
    const span = document.createElement("span");
    span.className = "badge";
    span.textContent = cartCount > 99 ? "99+" : String(cartCount);
    tab.appendChild(span);
  }
}

/** 用户信息（个人中心/结算页用）。带 30 秒内存缓存，避免来回切页重复请求。 */
let userCache = { at: 0, data: null };

export async function getUser(force = false) {
  if (!force && userCache.data && Date.now() - userCache.at < 30_000) return userCache.data;
  const data = await api("/user/info");
  userCache = { at: Date.now(), data };
  auth.saveUser(data);
  return data;
}

export function clearUser() {
  userCache = { at: 0, data: null };
}
