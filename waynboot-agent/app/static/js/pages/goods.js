/**
 * 商品详情页：图集 + 价格 + 规格选择 + 加购。
 *
 * 用到的接口：
 *   GET  /goods/detail/{id}   商品详情（info / specificationList / productList / attributes）
 *   POST /cart                有规格商品加购
 *   POST /cart/addDefaultGoodsProduct  无规格商品加购
 *
 * SKU 匹配规则（容易踩坑）：`productList[i].specifications` 是一个**数组**，
 * 顺序与 `specificationList` 的规格顺序一一对应。用户每选一个规格值，
 * 就去找 specifications 与"已选值数组"完全相等的那条货品，取它的价格与库存。
 */

import { api, pic, requireLogin } from "../api.js";
import { refreshCartCount } from "../store.js";
import { $, $$, emptyBox, esc, money, navBar, navEl, priceHtml, showLoading, hideLoading, toast } from "../ui.js";
import { commentCard } from "./comments.js";

export default async function goodsPage(root, params) {
  navBar({ title: "商品详情", sub: true, right: `<button class="icon-btn" id="toCart" aria-label="购物车">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <path d="M3 4h2l2.2 10.5a2 2 0 0 0 2 1.5h7.6a2 2 0 0 0 2-1.6L20 8H6"/><circle cx="10" cy="20" r="1.3"/><circle cx="17.5" cy="20" r="1.3"/></svg>
  </button>` });
  navEl("toCart").onclick = () => (location.hash = "#/cart");

  root.innerHTML = `<div id="body"><div class="card pad"><div class="skeleton sk-line" style="height:20px"></div></div></div>`;

  let data;
  try {
    data = await api(`/goods/detail/${params.id}`);
  } catch (e) {
    $("#body", root).innerHTML = emptyBox(e.message, "回到首页", "#/home");
    return;
  }

  const info = data?.info;
  if (!info) {
    // 上游对不存在的商品返回空对象而不是报错，所以在这里兜底
    $("#body", root).innerHTML = emptyBox("商品不存在或已下架", "回到首页", "#/home");
    return;
  }

  const specs = data.specificationList || [];
  const products = data.productList || [];
  const attributes = data.attributes || [];
  const gallery = (info.gallery || []).filter(Boolean);
  if (!gallery.length && info.picUrl) gallery.push(info.picUrl);

  // 评价与规格选择无关，只在进页面时拉一次；render() 会重绘，所以把结果缓存成 HTML
  let commentsHtml = `<div class="card pad" id="commentCard"><div class="skeleton sk-line" style="height:14px"></div></div>`;

  const state = {
    chosen: new Array(specs.length).fill(null), // 每个规格选中的值
    product: null,
    number: 1,
    // 没选满规格时用商品最低价做展示
    displayPrice: products.length ? Math.min(...products.map((p) => Number(p.price))) : info.retailPrice,
    maxNumber: 99,
  };

  render();
  bindEvents();
  loadComments();

  function render() {
    $("#body", root).innerHTML = `
      <div class="gallery">
        <div class="track" id="track">
          ${gallery.map((u) => `<img src="${esc(pic(u))}" alt="">`).join("")}
        </div>
        ${gallery.length > 1 ? `<div class="count" id="count">1/${gallery.length}</div>` : ""}
      </div>

      <div class="goods-head">
        <div class="row">
          <div>
            ${priceHtml(state.displayPrice)}
            ${
              // 专柜价只是"划线价"，用来对比；没有优惠（不比现价高）时就不显示，
              // 免得页面上出现两个让人分不清哪个才是要付的价格
              Number(info.counterPrice) > Number(state.displayPrice)
                ? `<span class="price old">专柜价 ¥${money(info.counterPrice)}</span>`
                : ""
            }
          </div>
          <span class="sales">已售 ${info.actualSales + (info.virtualSales || 0)}</span>
        </div>
        <h1 style="margin-top:10px">${esc(info.name)}</h1>
        ${info.brief ? `<p class="brief">${esc(info.brief)}</p>` : ""}
        <div style="display:flex;gap:6px;margin-top:4px">
          ${info.isNew ? `<span class="tag new">新品</span>` : ""}
          ${info.isHot ? `<span class="tag hot">热销</span>` : ""}
          <span class="tag gray">${esc(info.unit || "件")}</span>
        </div>
      </div>

      ${
        specs.length
          ? `<div class="card pad" id="skuCard">
              ${specs
                .map(
                  (spec, i) => `
                <div style="margin-bottom:12px">
                  <div style="font-size:12px;color:var(--muted);margin-bottom:8px">${esc(spec.name)}</div>
                  <div class="sku-row">
                    ${(spec.valueList || [])
                      .map(
                        (v) =>
                          `<span class="sku ${state.chosen[i] === v.value ? "on" : ""}" data-spec="${i}" data-value="${esc(v.value)}">${esc(v.value)}</span>`,
                      )
                      .join("")}
                  </div>
                </div>`,
                )
                .join("")}
              <div style="display:flex;align-items:center;justify-content:space-between;margin-top:4px">
                <span style="font-size:12px;color:var(--muted)">数量<span id="stockTip" style="margin-left:6px"></span></span>
                <span class="stepper">
                  <button id="minus" ${state.number <= 1 ? "disabled" : ""}>−</button>
                  <span id="num">${state.number}</span>
                  <button id="plus" ${state.number >= state.maxNumber ? "disabled" : ""}>＋</button>
                </span>
              </div>
            </div>`
          : `<div class="card">
              <div class="cell"><div class="cell-label">规格</div><div class="cell-value">默认（无规格商品）</div></div>
              <div class="cell" style="border-bottom:0">
                <div class="cell-label">数量</div>
                <span class="stepper">
                  <button id="minus" ${state.number <= 1 ? "disabled" : ""}>−</button>
                  <span id="num">${state.number}</span>
                  <button id="plus" ${state.number >= state.maxNumber ? "disabled" : ""}>＋</button>
                </span>
              </div>
            </div>`
      }

      ${commentsHtml}

      ${
        attributes.length
          ? `<div class="card">
              <div class="card-title">商品参数</div>
              <div class="attr-table">
                ${attributes.map((a) => `<div class="attr"><b>${esc(a.attribute)}</b><span>${esc(a.value)}</span></div>`).join("")}
              </div>
            </div>`
          : ""
      }

      ${
        info.detail
          ? `<div class="card">
              <div class="card-title">图文详情</div>
              <div class="rich" id="rich"></div>
            </div>`
          : ""
      }

      <div style="height:64px"></div>
      <div class="detail-bar">
        <button class="btn" id="addCart">加入购物车</button>
        <button class="btn primary" id="buyNow">立即购买</button>
      </div>`;

    // 详情是后端存的一段 HTML，里面有相对路径的图片，需要改写成转发地址
    const rich = $("#rich", root);
    if (rich && info.detail) {
      rich.innerHTML = info.detail;
      $$("img", rich).forEach((img) => {
        const src = img.getAttribute("src") || img.getAttribute("_src") || "";
        if (src) img.src = pic(src);
      });
    }

    const track = $("#track", root);
    if (track && gallery.length > 1) {
      const count = $("#count", root);
      track.addEventListener("scroll", () => {
        count.textContent = `${Math.round(track.scrollLeft / track.clientWidth) + 1}/${gallery.length}`;
      });
    }
    updateStockTip();
  }

  /** 拉评价摘要（统计 + 最新 3 条）。失败就整个区块不显示，不打扰用户看商品。 */
  async function loadComments() {
    const box = () => $("#commentCard", root);
    try {
      const [num, page] = await Promise.all([
        api("/comment/tagNum", { method: "POST", query: { goodsId: info.id } }),
        api("/comment/list", { method: "POST", query: { goodsId: info.id, pageNum: 1, pageSize: 3 } }),
      ]);
      const total = Number(num?.totalNum || 0);
      if (box()) box().outerHTML = buildComments(num, page?.records || [], total);
      commentsHtml = buildComments(num, page?.records || [], total);
    } catch {
      if (box()) box().remove();
      commentsHtml = "";
    }
  }

  function buildComments(num, records, total) {
    const good = Number(num?.goodsNum || 0);
    const rate = total ? Math.round((good / total) * 100) : 0;
    return `
      <div class="card" id="commentCard">
        <div class="card-title">
          商品评价${total ? `（${total}）` : ""}
          <a class="more" href="#/comments/${esc(info.id)}">查看全部 ›</a>
        </div>
        ${
          total
            ? `<div style="padding:0 14px 6px;display:flex;gap:8px;flex-wrap:wrap">
                 <span class="pill active">好评率 ${rate}%</span>
                 <span class="pill">好评 ${good}</span>
                 <span class="pill">中评 ${num?.middleNum || 0}</span>
                 <span class="pill">差评 ${num?.badNum || 0}</span>
                 ${num?.hasPictureNum ? `<span class="pill">有图 ${num.hasPictureNum}</span>` : ""}
               </div>`
            : `<div class="hint">还没有评价，买过的用户可以在「我的订单」里评价</div>`
        }
        ${records.map(commentCard).join("")}
      </div>`;
  }

  function updateStockTip() {
    const tip = $("#stockTip", root);
    if (!tip) return;
    if (specs.length && !state.product) {
      tip.textContent = "（请选择规格）";
      return;
    }
    tip.textContent = `（库存 ${state.product ? state.product.number : "充足"}）`;
  }

  /** 已选规格 → 匹配具体货品 */
  function matchProduct() {
    if (!specs.length) return null;
    if (state.chosen.some((v) => v === null)) return null;
    return (
      products.find(
        (p) => Array.isArray(p.specifications) && p.specifications.length === state.chosen.length && p.specifications.every((s, i) => s === state.chosen[i]),
      ) || null
    );
  }

  function bindEvents() {
    const body = $("#body", root);

    body.addEventListener("click", (e) => {
      const sku = e.target.closest(".sku");
      if (sku) {
        const i = Number(sku.dataset.spec);
        state.chosen[i] = state.chosen[i] === sku.dataset.value ? null : sku.dataset.value;
        state.product = matchProduct();
        if (state.product) {
          state.displayPrice = state.product.price;
          state.maxNumber = Math.max(1, Number(state.product.number) || 1);
          if (state.number > state.maxNumber) state.number = state.maxNumber;
        }
        render();
        return;
      }
      if (e.target.closest("#minus")) {
        state.number = Math.max(1, state.number - 1);
        render();
      }
      if (e.target.closest("#plus")) {
        if (state.number >= state.maxNumber) return toast("已达库存上限");
        state.number += 1;
        render();
      }
      if (e.target.closest("#addCart")) addToCart(false);
      if (e.target.closest("#buyNow")) addToCart(true);
    });
  }

  async function addToCart(goCart) {
    if (!requireLogin()) return; // 加购要凭证：mall 的购物车是挂在用户身上的
    if (specs.length && !state.product) return toast("请先选择商品规格");
    if (state.product && state.product.number <= 0) return toast("该规格已售罄");

    showLoading("正在加入购物车…");
    try {
      if (specs.length) {
        await api("/cart", {
          method: "POST",
          body: { goodsId: Number(info.id), productId: state.product.id, number: state.number },
        });
      } else {
        await api("/cart/addDefaultGoodsProduct", {
          method: "POST",
          body: { goodsId: Number(info.id), number: state.number },
        });
      }
      await refreshCartCount();
      toast("已加入购物车");
      if (goCart) location.hash = "#/cart";
    } catch (e) {
      toast(e.message);
    } finally {
      hideLoading();
    }
  }

}
