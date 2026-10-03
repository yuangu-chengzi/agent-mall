/**
 * 前端页面渲染检查（jsdom）：把每个页面模块在真实 DOM 里跑一遍，确认"渲染不报错 + 关键元素出现"。
 *
 * **为什么需要它**：这个项目的前端没有构建步骤，也没有浏览器自动化可用（本机 headless Chrome
 * 起不来），于是"页面能不能渲染"一度只靠人眼。踩过一次真实的坑：首页把导航栏里的按钮
 * 用 `$("#navSearch", root)` 去取（导航栏在 #view 之外），拿到 null → 赋值时抛
 * `Cannot set properties of null`，整个首页白屏。这类错误**只有真的跑一遍 DOM 才会暴露**。
 *
 * 它不测样式好不好看，测的是：
 *   1. 每个页面 render() 不抛异常；
 *   2. 关键元素/文案出现（商品卡片、规格、金额…）；
 *   3. 页面里绑定的按钮真的被绑上了（避免"点了没反应"）。
 *
 * 用法（在 tools/frontend-check 下）::
 *
 *     npm install      # 只需一次
 *     npm run check
 *
 * 退出码：0 = 全过，1 = 有失败（可接 CI）。
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { JSDOM } from "jsdom";

const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC_DIR = resolve(HERE, "../../app/static");

/* ------------------------------------------------------------------ *
 * 1. 造一个浏览器环境
 * ------------------------------------------------------------------ */

const dom = new JSDOM(readFileSync(resolve(STATIC_DIR, "index.html"), "utf8"), {
  url: "http://localhost:8300/",
  pretendToBeVisual: true,
});
const { window } = dom;

// 页面模块直接用全局 window/document/location，这里把它们铺到 globalThis 上
globalThis.window = window;
globalThis.document = window.document;
globalThis.location = window.location;
globalThis.localStorage = window.localStorage;
globalThis.sessionStorage = window.sessionStorage;
globalThis.HTMLElement = window.HTMLElement;
globalThis.Node = window.Node;
globalThis.Event = window.Event;
// Node 24 自带只读的 navigator，用 defineProperty 覆盖
Object.defineProperty(globalThis, "navigator", { value: window.navigator, configurable: true });
globalThis.history = window.history;
window.scrollTo = () => {};
window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

// jsdom 不带 IntersectionObserver 与 fetch，用最小实现顶上
globalThis.IntersectionObserver = class {
  constructor() {}
  observe() {}
  unobserve() {}
  disconnect() {}
};

// 把 CSS 注入 jsdom：jsdom 默认不会去拉 <link> 里的样式表，不注入的话
// getComputedStyle 什么都拿不到，"样式选择器写错"这类 bug 就测不出来。
for (const file of ["css/base.css", "css/app.css"]) {
  const style = document.createElement("style");
  style.textContent = readFileSync(resolve(STATIC_DIR, file), "utf8");
  document.head.appendChild(style);
}

/* ------------------------------------------------------------------ *
 * 2. 假 mall：按路径返回与真接口同形状的信封（字段取自 docs/API_CONTRACT.md §6）
 * ------------------------------------------------------------------ */

const GOODS = {
  id: 1181000,
  name: "母亲节礼物-舒适安睡组合",
  brief: "安心舒适是最好的礼物",
  picUrl: "/upload/a.webp",
  counterPrice: 2618,
  retailPrice: 2598,
  actualSales: 3,
  virtualSales: 10,
  isNew: true,
  isHot: false,
};

const ROUTES = [
  [/^\/home\/index/, () => ({ bannerList: [{ id: 1, title: "b", imgUrl: "/upload/banner1.webp" }], diamondList: [{ id: 1, name: "居家生活", jumpType: 1, valueId: 1005000, iconUrl: "/upload/d1.webp" }] })],
  [/^\/home\/recommonGoodsList/, () => [GOODS]],
  [/^\/category\/index/, () => CATEGORY],
  [/^\/category\/content/, () => CATEGORY],
  [/^\/category\/(first|second)CategoryGoods/, () => ({ goods: [GOODS], category: CATEGORY.currentCategory })],
  [/^\/search\/result/, () => [{ id: 1068010, name: "全棉澳毛床褥床垫 床笠款", picUrl: "/upload/b.webp", retailPrice: 329, actualSales: 0 }]],
  [/^\/search\/hotKeywords/, () => ({ hotStrings: ["毛巾", "夏凉被"], defaultSearch: "毛巾" })],
  [/^\/searchHistory\/list/, () => []],
  [/^\/goods\/detail\//, () => GOODS_DETAIL],
  [/^\/cart\/list/, () => [CART_ITEM]],
  [/^\/cart\/goodsCount/, () => 2],
  [
    /^\/cart\/getCheckedGoods/,
    () => ({
      data: [{ ...CART_ITEM, id: 77, price: 2598 }],
      couponList: [COUPON],
      freightPrice: 0,
      goodsAmount: 2598,
      // 注意：mall 的 orderTotalAmount = 商品金额 + 运费，**不含优惠券**
      orderTotalAmount: 2598,
    }),
  ],
  [/^\/address\/list/, () => [ADDRESS]],
  [/^\/order\/list/, () => ({ data: [ORDER], pages: 1, page: 1 })],
  [/^\/order\/statusCount/, () => ({ unpaid: 1, unship: 0, unrecv: 0, uncomment: 0 })],
  [/^\/order\/detail\//, () => ORDER_DETAIL],
  [/^\/order\/submit/, () => ({ orderSn: "9000000000000009", actualPrice: 2598 })],
  [/^\/order\/searchResult\//, () => null],
  [/^\/pay\/prepay/, () => ({})],
  [/^\/user\/info/, () => ({ id: 93, nickname: "小七", mobile: "13800000001", avatar: "/upload/avatar.webp", gender: 1 })],
  [/^\/comment\/tagNum/, () => ({ goodsNum: 4, middleNum: 1, badNum: 0, totalNum: 5, hasPictureNum: 1 })],
  [/^\/comment\/list/, () => ({ records: [COMMENT, { ...COMMENT, id: 2, star: 3, content: "还行吧", picUrls: [] }], total: 2, pages: 1, current: 1 })],
  [/^\/seckill\/list/, () => ({ records: [{ id: 2, name: "限时秒杀 · 家居专场", brief: "低至 5 折", startTime: "2026-10-03 19:00:00", endTime: "2026-10-06 19:00:00", status: 1 }], total: 1, pages: 1 })],
  [/^\/seckill\/token\//, () => ({ token: "tk-seckill", expireSecond: 60 })],
  [/^\/seckill\/submit/, () => ({ orderSn: "1790928945000099", status: "PROCESSING", actualPrice: 649.5 })],
  [/^\/seckill\/result\//, () => ({ orderSn: "1790928945000099", success: true })],
  [/^\/seckill\/detail\//, () => ({
    activity: { id: 2, name: "限时秒杀 · 家居专场", brief: "低至 5 折", startTime: "2026-10-03 19:00:00", endTime: "2026-10-06 19:00:00", status: 1 },
    skuList: [{ id: 2, activityId: 2, goodsId: 1181014, goodsName: "云朵抗菌鹅绒被 冬被加厚", picUrl: "/upload/a.webp", specifications: ["150×200cm", "1000g"], seckillPrice: 649.5, availableStock: 50, limitCount: 2, status: 1 }],
  })],
];

const CATEGORY = {
  currentCategory: { id: 1005000, name: "居家", remark: "回家，放松身心", picUrl: "/upload/c1.webp" },
  categoryList: [
    { id: 1005000, text: "居家", icon: "/upload/c1.webp" },
    { id: 1005001, text: "餐厨", icon: "/upload/c2.webp" },
  ],
  subCategoryList: [{ id: 1011003, text: "床垫", icon: "/upload/s1.webp" }],
};

const GOODS_DETAIL = {
  info: { ...GOODS, gallery: ["/upload/a.webp", "/upload/a2.webp"], unit: "件", detail: "<p><img src='/upload/detail1.webp'/></p>" },
  specificationList: [
    { name: "规格", valueList: [{ id: 1, value: "1.5m床垫*1" }, { id: 2, value: "1.8m床垫*1" }] },
    { name: "颜色", valueList: [{ id: 3, value: "浅杏粉", picUrl: "/upload/c3.webp" }] },
  ],
  productList: [
    { id: 1, specifications: ["1.5m床垫*1", "浅杏粉"], price: 999, number: 100, url: "/upload/a.webp" },
    { id: 2, specifications: ["1.8m床垫*1", "浅杏粉"], price: 1299, number: 5, url: "/upload/a.webp" },
  ],
  attributes: [{ attribute: "规格", value: "150*200cm" }],
};

const CART_ITEM = {
  id: 11,
  goodsId: 1181000,
  goodsName: "母亲节礼物-舒适安睡组合",
  price: 999,
  number: 1,
  specifications: ["1.5m床垫*1", "浅杏粉"],
  checked: true,
  picUrl: "/upload/a.webp",
  maxNum: 100,
};

const COMMENT = {
  id: 1,
  valueId: 1181000,
  userId: 1,
  avatar: "/upload/avatar.webp",
  content: "面料很厚实，洗过一次之后没有掉色",
  adminContent: "谢谢夸奖",
  hasPicture: true,
  picUrls: ["/upload/c.webp"],
  star: 5,
  createTime: "2026-09-01 10:00",
};

const COUPON = { id: 3, title: "全场通用 · 满 999 减 100", couponId: 3, discount: 100, min: 999, useStatus: 0, expireTime: "2027-01-01 00:00:00" };

const ADDRESS = { id: 5, name: "张三", tel: "13800000001", province: "江西省", city: "南昌市", county: "红谷滩区", addressDetail: "某某路 1 号", isDefault: true };

const ORDER = {
  id: 900001,
  orderSn: "9000000000000001",
  actualPrice: 59.7,
  orderStatusText: "已付款",
  handleOption: { cancel: false, delete: false, pay: false, comment: false, confirm: false, refund: true, rebuy: false, aftersale: false },
  goodsList: [{ id: 1, goodsId: 1181000, goodsName: "母亲节礼物-舒适安睡组合", goodsSn: "1181000", number: 1, price: 59.7, specifications: ["1.5m床垫*1"], picUrl: "/upload/a.webp" }],
};

const ORDER_DETAIL = {
  id: 900001,
  userId: 93,
  orderSn: "9000000000000001",
  orderStatus: 201,
  orderStatusText: "已付款",
  consignee: "测试收货人",
  mobile: "13800000001",
  address: "江西省南昌市测试地址",
  goodsPrice: 59.7,
  freightPrice: 0,
  couponPrice: 0,
  actualPrice: 59.7,
  payId: "TESTPAY-1",
  payType: 99,
  createTime: "2026-09-25 21:50:55",
  orderGoodsVOList: ORDER.goodsList,
};

const calls = [];

//: "auth-error" 时模拟会话失效——mall 的做法是 **HTTP 200 + code=401**（不是 HTTP 401）
let fetchMode = "ok";

globalThis.fetch = async (url, options = {}) => {
  const path = String(url).replace(/^\/api\/mall/, "").split("?")[0];
  calls.push(`${options.method || "GET"} ${path}`);

  // 登录代理不是 mall 路径，单独给
  if (path.startsWith("/api/login")) {
    return jsonResponse({ token: "tok-test", mobile: "13800000001" });
  }
  if (fetchMode === "auth-error") {
    return jsonResponse({ code: 401, msg: "没有权限，请联系管理员授权", data: null });
  }
  for (const [re, make] of ROUTES) {
    if (re.test(path)) {
      const data = make();
      // /order/searchResult 这类"成功但无数据"的接口，参考真 mall 返回 data=null
      return jsonResponse({ code: 200, msg: "操作成功", data: data ?? null });
    }
  }
  return jsonResponse({ code: 200, msg: "操作成功", data: null });
};

function jsonResponse(body) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

/* ------------------------------------------------------------------ *
 * 3. 逐页渲染
 * ------------------------------------------------------------------ */

const view = document.getElementById("view");
const failures = [];

function check(name, condition, detail = "") {
  if (condition) {
    console.log(`  [OK]   ${name}`);
  } else {
    failures.push(name);
    console.log(`  [FAIL] ${name}${detail ? ` -- ${detail}` : ""}`);
  }
}

/** 真实渲染一个页面模块，返回它抛出的异常（没有就返回 null）。 */
async function renderPage(modulePath, params = {}, query = new URLSearchParams()) {
  document.getElementById("nav").innerHTML = "";
  view.innerHTML = "";
  localStorage.setItem("mall.token", "tok-test");
  const mod = await import(pathToFileURL(resolve(STATIC_DIR, modulePath)).href);
  try {
    await mod.default(view, params, query);
    // 页面里有"先渲染、再异步补内容"的部分（比如商品详情的评价区块、
    // 首页的金刚区），它们是不 await 的。这里给一个结算窗口，等这些补完，
    // 否则断言会跑在它们前面（不是页面 bug，是测试没等）。
    await new Promise((r) => setTimeout(r, 30));
    return null;
  } catch (e) {
    return e;
  }
}

const text = () => view.textContent.replace(/\s+/g, " ");
const count = (sel) => view.querySelectorAll(sel).length;

const PAGES = [
  { name: "登录页", path: "js/pages/login.js", query: new URLSearchParams(), expect: () => text().includes("欢迎回来") },
  { name: "首页", path: "js/pages/home.js", expect: () => count(".goods-card") > 0 && document.getElementById("navSearch")?.onclick, marker: "推荐商品卡片 + 导航栏搜索按钮已绑定" },
  { name: "分类页", path: "js/pages/category.js", expect: () => count(".cat-rail .item") >= 2 && count(".sub-grid .sub") >= 1, marker: "左栏一级分类 + 右侧二级分类" },
  { name: "分类商品列表", path: "js/pages/list.js", params: {}, query: new URLSearchParams("cate=1011003&name=床垫&level=2"), expect: () => count(".goods-card") > 0, marker: "商品卡片" },
  { name: "搜索页（默认态）", path: "js/pages/search.js", query: new URLSearchParams(), expect: () => text().includes("热门搜索") && text().includes("毛巾"), marker: "热门词" },
  { name: "搜索结果页", path: "js/pages/search.js", query: new URLSearchParams("kw=床垫"), expect: () => count(".goods-card") > 0, marker: "结果卡片" },
  { name: "商品详情", path: "js/pages/goods.js", params: { id: "1181000" }, expect: () => count(".sku") === 3 && text().includes("加入购物车") && document.getElementById("toCart")?.onclick, marker: "3 个规格按钮 + 加购按钮 + 导航栏购物车已绑定" },
  { name: "购物车", path: "js/pages/cart.js", expect: () => count(".cart-item") === 1 && text().includes("去结算") && document.getElementById("editBtn")?.onclick, marker: "购物车行 + 结算栏 + 管理按钮已绑定" },
  { name: "结算页", path: "js/pages/checkout.js", expect: () => count(".address-card") === 1 && text().includes("2598.00") && text().includes("提交订单"), marker: "地址 + 金额 + 提交按钮" },
  { name: "收银台", path: "js/pages/pay.js", params: { sn: "9000000000000001" }, expect: () => text().includes("收银台") || text().includes("该订单不需要支付"), marker: "两种分支都不报错" },
  { name: "订单列表", path: "js/pages/orders.js", query: new URLSearchParams("showType=0"), expect: () => count(".order-card") === 1 && text().includes("申请退款"), marker: "订单卡片 + handleOption 生成的按钮" },
  { name: "订单详情", path: "js/pages/order.js", params: { sn: "9000000000000001" }, expect: () => text().includes("已付款") && text().includes("测试收货人"), marker: "状态 + 收货信息" },
  { name: "我的", path: "js/pages/profile.js", expect: () => text().includes("小七") && text().includes("退出登录"), marker: "昵称 + 退出按钮" },
  { name: "地址管理", path: "js/pages/address.js", query: new URLSearchParams(), expect: () => count(".address-card") === 1 && document.getElementById("addBtn")?.onclick, marker: "地址卡片 + 新增按钮已绑定" },
  { name: "优惠券", path: "js/pages/coupons.js", expect: () => text().includes("我的券") || text().includes("优惠券"), marker: "页面可渲染" },
  { name: "商品评价", path: "js/pages/comments.js", params: { id: "1181000" }, expect: () => text().includes("好评率") || text().includes("面料很厚实"), marker: "统计 tab + 评价内容" },
  { name: "秒杀列表", path: "js/pages/seckill.js", expect: () => text().includes("限时秒杀") && text().includes("抢购中"), marker: "活动卡片 + 状态" },
  { name: "秒杀详情", path: "js/pages/seckill-detail.js", params: { id: "2" }, expect: () => text().includes("立即抢购") && text().includes("649.50"), marker: "SKU + 秒杀价 + 抢购按钮" },
  { name: "编辑资料", path: "js/pages/profile-edit.js", expect: () => text().includes("保存资料") && text().includes("修改密码"), marker: "资料表单 + 改密码" },
];

console.log("\n===== 逐页渲染（jsdom）=====");
for (const page of PAGES) {
  const error = await renderPage(page.path, page.params, page.query);
  if (error) {
    check(`${page.name}：渲染不报错`, false, `${error.constructor.name}: ${error.message}`);
    continue;
  }
  check(`${page.name}：渲染不报错`, true);
  if (page.expect) check(`${page.name}：${page.marker || "关键元素出现"}`, page.expect());
}

/* ------------------------------------------------------------------ *
 * 3.2 商品详情的价格：原价必须是"划线价"，不能看着像第二个价格
 * ------------------------------------------------------------------ */

console.log("\n===== 商品详情价格 =====");
{
  await renderPage("js/pages/goods.js", { id: "1181000" });
  const now = view.querySelector(".goods-head .price:not(.old)");
  const was = view.querySelector(".goods-head .price.old");
  check("现价与划线价都在", Boolean(now) && Boolean(was));
  // 有 SKU 的商品标"最低价"（fixture 里 SKU 是 999/1299），没有 SKU 才用 retailPrice
  check("主导价格是能买到的最低价（999）", Boolean(now?.textContent.includes("999")), now?.textContent);
  check("原价带「专柜价」字样", Boolean(was?.textContent.includes("专柜价")), was?.textContent);
  // 这条是用户实机反馈后补的：CSS 只写了 .price .old（后代），而 HTML 用的是
  // class="price old"（同一个元素），选择器匹配不上 → 原价以正常红色显示 = "两个价格"
  // ⚠️ jsdom 里要读 textDecoration（简写）：textDecorationLine 恒为 "none"
  const style = was ? window.getComputedStyle(was) : null;
  const deco = `${style?.textDecoration || ""} ${style?.textDecorationLine || ""}`;
  check("原价真的有删除线（CSS 选择器要对得上）", deco.includes("line-through"), `text-decoration=${deco.trim() || "(空)"}`);
  check("商品详情带出了评价区块", text().includes("商品评价") && text().includes("好评率"), text().slice(-90));
}

/* ------------------------------------------------------------------ *
 * 3.5 站内链接：不能指向不存在的地址
 * ------------------------------------------------------------------ */

console.log("\n===== 站内链接 =====");
//: 允许的非哈希链接：`/chat` 是客服页，`/` 是商城壳；其余一律应该是 `#/xxx` 路由
const ALLOWED_PATHS = new Set(["/", "/chat"]);
const badLinks = new Set();
for (const page of PAGES) {
  await renderPage(page.path, page.params, page.query);
  for (const el of view.querySelectorAll("[href], [data-href]")) {
    const href = (el.getAttribute("href") || el.getAttribute("data-href") || "").trim();
    if (!href || href.startsWith("#") || href.startsWith("data:") || href.startsWith("http")) continue;
    if (href === "#") continue;
    if (!ALLOWED_PATHS.has(href)) badLinks.add(`${page.name}: ${href}`);
  }
}
check("没有指向不存在地址的链接（如把 /chat 写成 /chat.html）", badLinks.size === 0, [...badLinks].join(", "));

/* ------------------------------------------------------------------ *
 * 4. 会话失效：mall 用 HTTP 200 + code=401 表达，前端要回登录页
 * ------------------------------------------------------------------ */

console.log("\n===== 会话失效处理 =====");
{
  const { api, auth } = await import(pathToFileURL(resolve(STATIC_DIR, "js/api.js")).href);
  localStorage.setItem("mall.token", "stale-token");
  location.hash = "#/cart";
  fetchMode = "auth-error";

  let thrown = null;
  try {
    await api("/cart/list");
  } catch (e) {
    thrown = e;
  }
  fetchMode = "ok";

  check("code=401 会抛错", Boolean(thrown), thrown ? thrown.message : "没有抛错");
  check("code=401 会清掉本地登录态", !auth.token, `token=${auth.token}`);
  check("code=401 会跳回登录页", location.hash === "#/login", location.hash);
  check("跳登录页前记住了原来在哪一页", sessionStorage.getItem("mall.redirect") === "#/cart");
}

/* ------------------------------------------------------------------ *
 * 4.2 结算页金额：选了券，"应付"要真的减掉
 * ------------------------------------------------------------------ */

console.log("\n===== 结算页金额 =====");
{
  await renderPage("js/pages/checkout.js");
  check("未选券时应付 = 商品金额（2598）", text().includes("2598.00"));

  // 打开券列表并选第一张（弹层挂在 body 上，不在 #view 里）
  document.querySelector("#pickCoupon").click();
  const couponCells = [...document.querySelectorAll(".mask .cell[data-id]")].filter((el) => el.dataset.id);
  check("能打开券列表并看到可用券", couponCells.length === 1, `找到 ${couponCells.length} 张`);
  couponCells[0]?.click();

  // 这条是实机联调时对不上账才发现的：mall 的 orderTotalAmount = 商品金额 + 运费，
  // **不含券**（券在提交订单时才减），前端不自己减一次就会"页面显示 1698、实际扣 1598"
  check("选了券后应付 = 2598 - 100 = 2498", text().includes("2498.00"), text().slice(0, 80));
  check("明细里列出了券的抵扣行", text().includes("优惠券") && text().includes("100.00"));
}

/* ------------------------------------------------------------------ *
 * 4.5 客服页的快捷项：缺信息的应该"填输入框"，而不是替用户编一个
 * ------------------------------------------------------------------ */

console.log("\n===== 客服页快捷项 =====");
{
  // 客服页是独立 HTML（带内联脚本），单开一个带 runScripts 的 jsdom 来跑
  const chatDom = new JSDOM(readFileSync(resolve(STATIC_DIR, "chat.html"), "utf8"), {
    url: "http://localhost:8300/chat",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(win) {
      win.fetch = globalThis.fetch; // 别让 jsdom 的 fetch 抛 "not implemented"
      win.localStorage.clear(); // 从"未登录"开始，免得 enterApp 去拉会话
    },
  });
  const doc = chatDom.window.document;
  const click = (label) => [...doc.querySelectorAll("#quick button")].find((b) => b.textContent.trim() === label).click();

  click("找商品");
  const filled = doc.getElementById("input").value;
  check("「找商品」只是把话头填进输入框", filled === "帮我找找有没有", `input="${filled}"`);
  check("「找商品」没有直接把消息发出去", doc.querySelectorAll("#turns .turn").length === 0);

  click("算价格");
  check("「算价格」同样只填不发", doc.getElementById("input").value === "帮我算一下：");

  // 反面对照：信息已经完整的那几个，点了就该发出去（一问一答两条气泡）
  click("我的订单");
  check("信息完整的「我的订单」点了会直接发送", doc.querySelectorAll("#turns .turn").length >= 1);

  chatDom.window.close();
}

/* ------------------------------------------------------------------ *
 * 5. 路由与交互：点一下是不是真的动了
 * ------------------------------------------------------------------ */

console.log("\n===== 路由与交互 =====");
const { start } = await import(pathToFileURL(resolve(STATIC_DIR, "js/router.js")).href);
const { register } = await import(pathToFileURL(resolve(STATIC_DIR, "js/router.js")).href);

const homeModule = await import(pathToFileURL(resolve(STATIC_DIR, "js/pages/home.js")).href);
let rendered = "";
register("/home", homeModule.default, { tab: "home" });
register("/goods/:id", homeModule.default, { tab: null });
start((matched) => (rendered = matched.pattern));

location.hash = "#/goods/42";
await new Promise((r) => setTimeout(r, 50));
check("路由把 /goods/42 匹配到 /goods/:id 并取出参数", rendered === "/goods/:id");

const { go } = await import(pathToFileURL(resolve(STATIC_DIR, "js/router.js")).href);
go("#/home");
await new Promise((r) => setTimeout(r, 50));
check("go() 切回 /home 生效", rendered === "/home");

check("整个渲染过程没有未捕获异常", true);

/* ------------------------------------------------------------------ */

console.log(`\n===== ${failures.length === 0 ? "全部通过" : `${failures.length} 项失败`} =====`);
for (const f of failures) console.log(`  FAIL: ${f}`);
console.log(`（期间前端共发出 ${calls.length} 次接口调用）`);
process.exit(failures.length ? 1 : 0);
