/**
 * 分类页：左边一级分类竖排，右边是该分类下的二级分类。
 *
 * 用到的接口：
 *   GET /category/index      首屏（返回分类列表 + 第一个分类的详情）
 *   GET /category/content?id 某个一级分类的详情（含二级分类列表）
 *
 * 点二级分类 → 商品列表走 `/category/secondCategoryGoods`；
 * 点"全部商品" → 走 `/category/firstCategoryGoods`（会把该一级分类下所有子类的商品都带出来）。
 */

import { api, pic } from "../api.js";
import { $, $$, emptyBox, esc, navBar, skeletonGrid } from "../ui.js";

const CHEV = `<svg class="chev" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>`;

export default async function categoryPage(root) {
  navBar({ title: "分类", sub: false });

  root.innerHTML = `
    <div class="cat-wrap">
      <div class="cat-rail" id="rail"></div>
      <div class="cat-main" id="main">${skeletonGrid(2)}</div>
    </div>`;

  let index;
  try {
    index = await api("/category/index");
  } catch (e) {
    $("#main", root).innerHTML = emptyBox(e.message);
    return;
  }

  const categories = index.categoryList || [];
  if (!categories.length) {
    $("#main", root).innerHTML = emptyBox("暂无分类");
    return;
  }

  $("#rail", root).innerHTML = categories
    .map((c, i) => `<div class="item ${i ? "" : "active"}" data-id="${c.id}">${esc(c.text)}</div>`)
    .join("");

  $("#rail", root).addEventListener("click", (e) => {
    const item = e.target.closest(".item");
    if (!item || item.classList.contains("active")) return;
    $$("#rail .item", root).forEach((el) => el.classList.toggle("active", el === item));
    loadCategory(item.dataset.id);
  });

  async function loadCategory(id) {
    const main = $("#main", root);
    main.innerHTML = skeletonGrid(2);

    let data;
    try {
      data = await api("/category/content", { query: { id } });
    } catch (e) {
      main.innerHTML = emptyBox(e.message);
      return;
    }

    const current = data.currentCategory || {};
    const subs = data.subCategoryList || [];
    const allHref = `#/list?cate=${current.id}&name=${encodeURIComponent(current.name || "")}&level=1`;

    main.innerHTML = `
      <div class="head">
        ${current.picUrl ? `<img src="${esc(pic(current.picUrl))}" alt="">` : ""}
        <div>
          <h2>${esc(current.name || "")}</h2>
          <p>${esc(current.remark || "")}</p>
        </div>
      </div>
      <div class="cell" data-href="${allHref}" style="margin:0 12px 12px;border-radius:var(--radius);border-bottom:0">
        <div class="cell-label">全部商品</div>${CHEV}
      </div>
      ${
        subs.length
          ? `<div class="sub-grid">${subs
              .map(
                (s) => `<div class="sub" data-href="#/list?cate=${s.id}&name=${encodeURIComponent(s.text)}&level=2">
                  ${s.icon ? `<img src="${esc(pic(s.icon))}" alt="">` : `<div style="width:58px;height:58px;margin:0 auto 6px;border-radius:50%;background:var(--bg)"></div>`}
                  <span>${esc(s.text)}</span>
                </div>`,
              )
              .join("")}</div>`
          : `<div class="hint">该分类下暂无二级分类，可点上方「全部商品」查看</div>`
      }`;

    $$("[data-href]", main).forEach((el) => (el.onclick = () => (location.hash = el.dataset.href)));
  }

  await loadCategory(categories[0].id);
}
