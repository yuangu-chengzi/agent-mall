/**
 * 分类商品列表页（`#/list?cate=1011003&name=床垫&level=2`）。
 *
 * 用到的接口：
 *   GET /category/firstCategoryGoods?cateId=   一级分类（含子类商品）
 *   GET /category/secondCategoryGoods?cateId=  二级分类
 *
 * 注意：这两个接口返回 `{goods, category}`，**没有分页信息**，
 * 所以「还有没有下一页」只能靠"本页条数是否等于 pageSize"来判断。
 */

import { api } from "../api.js";
import { onLeave } from "../router.js";
import { $, navBar, skeletonGrid } from "../ui.js";
import { bindGoodsCards, onReachBottom, pagedGoods } from "../widgets.js";

export default async function listPage(root, params, query) {
  const cateId = query.get("cate") || "0";
  const name = query.get("name") || "商品列表";
  const level = query.get("level") === "1" ? 1 : 2;
  const path = level === 1 ? "/category/firstCategoryGoods" : "/category/secondCategoryGoods";

  navBar({ title: name, sub: true });

  root.innerHTML = `
    <div id="goodsBox">${skeletonGrid(4)}</div>
    <div id="sentinel" style="height:1px"></div>
    <div class="hint" id="loadTip" style="text-align:center"></div>`;

  const list = pagedGoods({
    box: $("#goodsBox", root),
    tip: $("#loadTip", root),
    fetchPage: async (pageNum, pageSize) => {
      const data = await api(path, { query: { cateId, pageNum, pageSize } });
      return data?.goods || [];
    },
  });

  bindGoodsCards($("#goodsBox", root));
  const stopScroll = onReachBottom($("#sentinel", root), () => list.load());
  onLeave(stopScroll);

  await list.load();
}
