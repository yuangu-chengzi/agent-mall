/**
 * router.js —— 哈希路由（`#/goods/1181000`）。
 *
 * 为什么用哈希而不是 History API：`#` 后面的内容不参与服务端请求，
 * 刷新页面时后端只要返回同一个 index.html 就行，不需要给每条路径配服务端路由。
 */

import { auth } from "./api.js";
import { $, toast } from "./ui.js";

const routes = [];
let current = null;
let onRendered = null;

// 自己维护一个访问栈：哈希路由下 history.back() 会退出页面而不是回到上一屏，
// 子页面的"返回"要的是上一屏，所以由路由自己记录。
const stack = [];
const cleaners = [];

/**
 * 注册一条路由。
 *
 * @param {string} pattern 形如 `/goods/:id`
 * @param {(root: HTMLElement, params: object, query: URLSearchParams) => Promise<void>} render
 * @param {{auth?: boolean, tab?: string, title?: string}} opts
 *        auth=true 需要登录；tab=底部标签栏高亮项；title=导航栏标题
 */
export function register(pattern, render, opts = {}) {
  const keys = [];
  const regex = new RegExp(
    "^" +
      pattern
        .split("/")
        .map((seg) => {
          if (seg.startsWith(":")) {
            keys.push(seg.slice(1));
            return "([^/]+)";
          }
          return seg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        })
        .join("/") +
      "$",
  );
  routes.push({ pattern, regex, keys, render, opts });
}

/** 解析 `#/goods/1?a=2` → {path:'/goods/1', query:URLSearchParams} */
function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/home";
  const [path, search = ""] = raw.split("?");
  return { path: path || "/home", query: new URLSearchParams(search) };
}

export function start(renderedHook) {
  onRendered = renderedHook;
  window.addEventListener("hashchange", () => resolve(true));
  resolve(false);
}

/** 跳转。 */
export function go(hash) {
  if (location.hash === hash) resolve(true);
  else location.hash = hash;
}

/** 返回上一屏（没有上一屏时回首页）。页面顶部的返回箭头用它。 */
export function back() {
  stack.pop(); // 丢掉当前页
  const prev = stack.pop();
  go(prev || "#/home");
}

/** 当前路由信息。 */
export function currentRoute() {
  return current;
}

/** 页面注册清理函数（定时器、轮询等），切页时统一执行。 */
export function onLeave(fn) {
  cleaners.push(fn);
}

function runCleaners() {
  while (cleaners.length) {
    try {
      cleaners.pop()();
    } catch (e) {
      console.warn("[router] 清理钩子异常", e);
    }
  }
}

function resolve(isNavigation) {
  const { path, query } = parseHash();
  const matched = routes.find((r) => r.regex.test(path));

  if (!matched) {
    location.hash = "#/home";
    return;
  }

  const values = matched.regex.exec(path).slice(1);
  const params = {};
  matched.keys.forEach((key, i) => (params[key] = decodeURIComponent(values[i])));

  // 需要登录的页面：记下想去哪，登录后跳回去
  if (matched.opts.auth && !auth.logged) {
    sessionStorage.setItem("mall.redirect", location.hash);
    location.hash = "#/login";
    return;
  }

  runCleaners();
  current = { ...matched, params, query, path };
  if (isNavigation) stack.push(location.hash);

  const view = $("#view");
  view.innerHTML = "";
  window.scrollTo(0, 0);

  Promise.resolve(matched.render(view, params, query))
    .catch((e) => {
      console.error("[router] 页面渲染失败", e);
      view.innerHTML = `<div class="empty"><div class="big">!</div><p>${e.message || "页面加载失败"}</p>
        <button class="btn" onclick="location.reload()">重新加载</button></div>`;
      toast(e.message || "页面加载失败");
    })
    .finally(() => onRendered && onRendered(matched));
}
