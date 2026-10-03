/**
 * app.js —— 装配入口：路由表 + 底部标签栏 + 客服浮标。
 *
 * 页面模块都只导出 `render(root, params, query)`，由 router.js 统一调度；
 * 这里负责"哪条路径对应哪个页面"以及全站性的壳（标签栏、浮标）。
 */

import { auth, setAuthExpiredHandler } from "./api.js";
import { clearUser } from "./store.js";
import { back, currentRoute, register, start } from "./router.js";
import { $, ICON, setBackHandler } from "./ui.js";
import { refreshCartCount } from "./store.js";
import { toast } from "./ui.js";

import addressPage from "./pages/address.js";
import cartPage from "./pages/cart.js";
import categoryPage from "./pages/category.js";
import checkoutPage from "./pages/checkout.js";
import commentsPage from "./pages/comments.js";
import couponsPage from "./pages/coupons.js";
import goodsPage from "./pages/goods.js";
import homePage from "./pages/home.js";
import listPage from "./pages/list.js";
import loginPage from "./pages/login.js";
import orderDetailPage from "./pages/order.js";
import ordersPage from "./pages/orders.js";
import payPage from "./pages/pay.js";
import profileEditPage from "./pages/profile-edit.js";
import profilePage from "./pages/profile.js";
import searchPage from "./pages/search.js";
import seckillListPage from "./pages/seckill.js";
import seckillDetailPage from "./pages/seckill-detail.js";

/** 底部标签栏。tab 值要和路由的 opts.tab 对得上。 */
const TABS = [
  { key: "home", label: "首页", href: "#/home", icon: ICON.home },
  { key: "category", label: "分类", href: "#/category", icon: ICON.grid },
  { key: "cart", label: "购物车", href: "#/cart", icon: ICON.cart, badge: true },
  { key: "mine", label: "我的", href: "#/profile", icon: ICON.user },
];

// 路由表：auth=true 的页面没登录会被送去登录页
register("/login", loginPage, { tab: null, title: "登录" });
register("/home", homePage, { tab: "home" });
register("/category", categoryPage, { tab: "category" });
register("/list", listPage, { tab: "category", title: "商品列表" });
register("/search", searchPage, { tab: null, title: "搜索" });
register("/goods/:id", goodsPage, { tab: null, title: "商品详情" });
register("/cart", cartPage, { tab: "cart", auth: true });
register("/checkout", checkoutPage, { tab: null, auth: true, title: "确认订单" });
register("/pay/:sn", payPage, { tab: null, auth: true, title: "收银台" });
register("/orders", ordersPage, { tab: "mine", auth: true, title: "我的订单" });
register("/order/:sn", orderDetailPage, { tab: null, auth: true, title: "订单详情" });
register("/profile", profilePage, { tab: "mine", auth: true });
register("/profile/edit", profileEditPage, { tab: null, auth: true, title: "编辑资料" });
register("/address", addressPage, { tab: null, auth: true, title: "收货地址" });
register("/coupons", couponsPage, { tab: null, auth: true, title: "优惠券" });
register("/comments/:id", commentsPage, { tab: null, title: "商品评价" });
register("/seckill", seckillListPage, { tab: null, title: "限时秒杀" });
register("/seckill/:id", seckillDetailPage, { tab: null, title: "秒杀详情" });

function renderTabbar(activeKey) {
  const bar = $("#tabbar");
  bar.classList.toggle("hidden", !activeKey);
  if (!activeKey) return;
  bar.innerHTML = TABS.map(
    (t) =>
      `<button class="tab ${t.key === activeKey ? "active" : ""}" id="tab-${t.key}" data-href="${t.href}">
        ${t.icon}<span>${t.label}</span>
      </button>`,
  ).join("");
  bar.querySelectorAll(".tab").forEach((el) => {
    el.onclick = () => {
      // 已经在当前 tab 上就重新加载一次（相当于"再点一下刷新"）
      if (currentRoute()?.opts?.tab === el.id.replace("tab-", "")) location.reload();
      else location.hash = el.dataset.href;
    };
  });
}

setBackHandler(back);

// 会话失效（mall 用 HTTP 200 + code=401 表达）时：清掉用户信息缓存并提示，
// 页面跳转由 api.js 统一处理
setAuthExpiredHandler(() => {
  clearUser();
  toast("登录已过期，请重新登录");
});

start((matched) => {
  renderTabbar(matched.opts.tab || null);

  // 客服浮标：登录后常驻，聊天页自己没有（那是独立页面，不经过这个路由）
  const fab = $("#cs-fab");
  fab.classList.toggle("with-bar", Boolean(matched.opts.tab));

  if (auth.logged) refreshCartCount();
});

// 没登录时先落在首页（可以逛，但加购/下单会被送去登录）
if (!location.hash) location.hash = "#/home";
