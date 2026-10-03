/**
 * widgets.js —— 首页/分类/搜索三个列表页共用的零件。
 *
 * 抽出来的理由：这三处的商品卡片长得一样、分页方式也一样，
 * 复制三份的话改一次样式要改三个地方。
 */

import { emptyBox, esc, imgHtml, priceHtml, skeletonGrid } from "./ui.js";

/** 商品卡片。字段做了兼容：列表接口有的给 retailPrice、有的给 price。 */
export function goodsCard(g) {
  return `
    <div class="goods-card" data-id="${g.id}">
      ${imgHtml(g.picUrl || g.url, "thumb")}
      <div class="body">
        <div class="name">${esc(g.name)}</div>
        <div class="brief">${esc(g.brief || "")}</div>
        <div class="foot">
          ${priceHtml(g.retailPrice ?? g.price)}
          <span class="sales">${g.actualSales || 0} 人付款</span>
        </div>
      </div>
    </div>`;
}

export function goodsGrid(items) {
  return `<div class="grid">${items.map(goodsCard).join("")}</div>`;
}

/**
 * 商品网格的点击跳转。绑在**页面内的容器**上做事件委托，
 * 这样滚动加载出来的新卡片也生效，而且容器随页面一起销毁、不会在 #view 上越挂越多。
 */
export function bindGoodsCards(container) {
  container.addEventListener("click", (e) => {
    const card = e.target.closest(".goods-card");
    if (card) location.hash = `#/goods/${card.dataset.id}`;
  });
}

/**
 * 分页商品列表。
 *
 * 上游的分类/推荐接口**不返回总页数**，所以用"本页条数 < pageSize 即到底"判断结束
 * —— 这也是为什么 pageSize 要如实传给后端：它是结束判断的依据。
 *
 * @param {{box: HTMLElement, tip?: HTMLElement, fetchPage: (pageNum:number, pageSize:number)=>Promise<any[]>,
 *          pageSize?: number, emptyText?: string}} opts
 */
export function pagedGoods({ box, tip, fetchPage, pageSize = 10, emptyText = "没有找到商品" }) {
  let pageNum = 1;
  let finished = false;
  let loading = false;

  async function load() {
    if (loading || finished) return;
    loading = true;
    if (pageNum === 1) box.innerHTML = skeletonGrid(4);
    try {
      const items = (await fetchPage(pageNum, pageSize)) || [];
      if (pageNum === 1) {
        box.innerHTML = items.length ? goodsGrid(items) : emptyBox(emptyText);
      } else if (items.length) {
        const grid = box.querySelector(".grid");
        if (grid) grid.insertAdjacentHTML("beforeend", items.map(goodsCard).join(""));
      }
      pageNum += 1;
      if (items.length < pageSize) {
        finished = true;
        if (tip) tip.textContent = box.querySelector(".goods-card") ? "— 到底啦 —" : "";
      }
    } catch (e) {
      if (pageNum === 1) box.innerHTML = emptyBox(e.message || "加载失败");
      if (tip) tip.textContent = e.message || "";
      finished = true;
    } finally {
      loading = false;
    }
  }

  return {
    load,
    get finished() {
      return finished;
    },
  };
}

/** 滚动到接近底部时触发回调，返回一个断开函数（切页时记得调用）。 */
export function onReachBottom(sentinel, callback, rootMargin = "200px") {
  const io = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) callback();
    },
    { rootMargin },
  );
  io.observe(sentinel);
  return () => io.disconnect();
}
