/**
 * api.js —— 唯一的网络出口。
 *
 * 浏览器只跟 Agent（8300）说话，不直接连 mall（82）：
 * Agent 上的 `/api/mall/**` 会把请求原样转发给 mall（见 app/mall/proxy.py），
 * 这样就绕开了跨域，也顺便不用把 mall 暴露给浏览器。
 *
 * mall 的响应统一是 `{code, msg, data}` 信封，且**业务失败也返回 HTTP 200**，
 * 所以判断成功与否必须看 code，不能只看 HTTP 状态码。
 */

export class ApiError extends Error {
  constructor(message, code) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

const TOKEN_KEY = "mall.token";
const MOBILE_KEY = "mall.mobile";
const USER_KEY = "mall.user";

/**
 * mall 表示"没登录/没权限"的业务码。
 *
 * ⚠️ 关键：mall 的业务失败**也是 HTTP 200**，登录态失效的 401/403 只出现在响应体的 `code` 里
 * （见 docs/API_CONTRACT.md 硬规则 1）。只判断 `res.status` 的话，会话过期时用户看到的
 * 只是一句"没有权限，请联系管理员授权"的提示，人却还停在已登录的界面上——点哪都不对。
 */
const AUTH_CODES = new Set([401, 403]);

let onAuthExpired = () => {};

/** 注册"会话失效"时的额外处理（清缓存、提示），由 app.js 装配，避免 api ⇄ store 循环依赖。 */
export function setAuthExpiredHandler(fn) {
  onAuthExpired = fn;
}

let expiring = false;

/** 会话失效：清本地登录态 → 记住当前页 → 回登录页（并发请求只处理一次）。 */
function expireSession(message) {
  auth.clear();
  if (!expiring) {
    expiring = true;
    const here = location.hash;
    if (here && !here.startsWith("#/login")) sessionStorage.setItem("mall.redirect", here);
    onAuthExpired();
    location.hash = "#/login";
    setTimeout(() => (expiring = false), 1000);
  }
  return new ApiError(message || "登录已过期，请重新登录", 401);
}

export const auth = {
  get token() {
    return localStorage.getItem(TOKEN_KEY) || "";
  },
  get mobile() {
    return localStorage.getItem(MOBILE_KEY) || "";
  },
  get user() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  },
  save(token, mobile) {
    localStorage.setItem(TOKEN_KEY, token);
    if (mobile) localStorage.setItem(MOBILE_KEY, mobile);
  },
  saveUser(user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user || null));
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(MOBILE_KEY);
    localStorage.removeItem(USER_KEY);
  },
  get logged() {
    return Boolean(this.token);
  },
};

/**
 * 需要登录才能做的动作（加购、下单…）先问一句。
 * 没登录就送去登录页，并记下"原本想干什么"。
 */
export function requireLogin() {
  if (auth.logged) return true;
  sessionStorage.setItem("mall.redirect", location.hash || "#/home");
  location.hash = "#/login";
  return false;
}

/** 拼查询串（跳过空值，避免把 undefined 发成字符串 "undefined"）。 */
function buildQuery(query) {
  if (!query) return "";
  const parts = [];
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

/**
 * 调 mall（经 Agent 转发）。
 *
 * @param {string} path  mall 的相对路径，例如 `/goods/detail/1`
 * @param {object} opts  {method, body, query, raw}
 * @returns {Promise<any>} 信封里的 data
 */
export async function api(path, opts = {}) {
  const { method = "GET", body, query, raw = false } = opts;
  const headers = {};
  if (auth.token) headers.Authorization = auth.token;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res;
  try {
    res = await fetch(`/api/mall${path}${buildQuery(query)}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError("网络连接失败，请检查后端服务是否在运行", -1);
  }

  if (res.status === 401 || res.status === 403) throw expireSession();
  if (raw) return res;

  let envelope;
  try {
    envelope = await res.json();
  } catch {
    throw new ApiError(`服务返回异常（HTTP ${res.status}）`, res.status);
  }

  // 业务错误也是 HTTP 200，靠 code 判断
  if (envelope && typeof envelope === "object" && "code" in envelope) {
    if (envelope.code !== 200) {
      // 登录态失效（HTTP 200 + code=401/403）要当"会话过期"处理，而不是普通报错
      if (AUTH_CODES.has(envelope.code)) throw expireSession(envelope.msg);
      throw new ApiError(envelope.msg || "操作失败", envelope.code);
    }
    return envelope.data;
  }
  return envelope;
}

/** 发送短信验证码（走 Agent 的登录代理）。 */
export async function sendLoginCode(mobile) {
  const res = await fetch("/api/login/code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mobile }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(readDetail(body) || "验证码发送失败", res.status);
  return body;
}

/** 登录：拿 mall 签发的 Sa-Token。 */
export async function login(mobile, yzm) {
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mobile, yzm }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(readDetail(body) || "登录失败", res.status);
  auth.save(body.token, body.mobile || mobile);
  return body;
}

function readDetail(body) {
  if (!body) return "";
  if (typeof body.detail === "string") return body.detail;
  if (body.detail && body.detail.message) return body.detail.message;
  return "";
}

/**
 * 图片地址：mall 返回的 `/upload/xxx.webp` 要经 Agent 转发才能取到。
 * 第三方外链（http 开头）原样返回。
 */
export function pic(url) {
  if (!url) return PLACEHOLDER;
  if (/^https?:\/\//.test(url) || url.startsWith("data:")) return url;
  const path = url.startsWith("/") ? url : `/${url}`;
  return `/api/mall${path}`;
}

/** 没有图时的占位（内联 SVG，省一次请求）。 */
export const PLACEHOLDER =
  "data:image/svg+xml," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#f2f3f5"/><text x="60" y="66" font-size="13" fill="#c2c6cc" text-anchor="middle">暂无图片</text></svg>`,
  );
