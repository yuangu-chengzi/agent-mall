/**
 * 商品评价列表页（`#/comments/商品id`）。
 *
 * 用到的接口（注意都是 POST，参数走 query string）：
 *   POST /comment/tagNum?goodsId=   好评/中评/差评/有图 的数量统计
 *   POST /comment/list?goodsId=&tagType=&pageNum=&pageSize
 *        tagType 的语义由服务端 SQL 决定：1=好评(star 4~5) 2=中评(star 3) 3=差评(star 1~2)（见 CommentMapper.xml）
 *
 * 评价数据本身是上游带进来的（shop_comment 表 50 条），前端只负责展示。
 */

import { api, pic } from "../api.js";
import { onLeave } from "../router.js";
import { $, $$, emptyBox, esc, navBar, skeletonList } from "../ui.js";
import { onReachBottom } from "../widgets.js";

const TABS = [
  { type: 0, name: "全部", key: "totalNum" },
  { type: 1, name: "好评", key: "goodsNum" },
  { type: 2, name: "中评", key: "middleNum" },
  { type: 3, name: "差评", key: "badNum" },
];

export default async function commentsPage(root, params) {
  const goodsId = params.id;
  navBar({ title: "商品评价", sub: true });

  root.innerHTML = `
    <div class="tabs" id="tabs"></div>
    <div id="box">${skeletonList(3)}</div>
    <div id="sentinel" style="height:1px"></div>
    <div class="hint" id="tip" style="text-align:center"></div>`;

  let tagType = 0;
  let pageNum = 1;
  let totalPages = 1;
  let loading = false;

  // 统计是锦上添花，失败就当没有
  api("/comment/tagNum", { method: "POST", query: { goodsId } })
    .then((num) => renderTabs(num || {}))
    .catch(() => renderTabs({}));

  function renderTabs(num) {
    $("#tabs", root).innerHTML = TABS.map((t) => {
      const count = num[t.key];
      return `<div class="t ${t.type === tagType ? "active" : ""}" data-type="${t.type}">${esc(t.name)}${count ? ` (${count})` : ""}</div>`;
    }).join("");
    $$("#tabs .t", root).forEach((el) => {
      el.onclick = () => {
        if (Number(el.dataset.type) === tagType) return;
        tagType = Number(el.dataset.type);
        pageNum = 1;
        totalPages = 1;
        renderTabs(num);
        load();
      };
    });
  }

  async function load() {
    if (loading || pageNum > totalPages) return;
    loading = true;
    const box = $("#box", root);
    try {
      const res = await api("/comment/list", {
        method: "POST",
        query: { goodsId, tagType, pageNum, pageSize: 10 },
      });
      const records = res?.records || [];
      totalPages = Number(res?.pages ?? 1) || 1;
      if (pageNum === 1) {
        box.innerHTML = records.length ? "" : emptyBox(tagType ? "这个分类下还没有评价" : "还没有评价");
      }
      box.insertAdjacentHTML("beforeend", records.map(commentCard).join(""));
      pageNum += 1;
      $("#tip", root).textContent = pageNum > totalPages && records.length ? "— 到底啦 —" : "";
    } catch (e) {
      if (pageNum === 1) box.innerHTML = emptyBox(e.message);
      $("#tip", root).textContent = e.message || "";
      totalPages = 0;
    } finally {
      loading = false;
    }
  }

  const stopScroll = onReachBottom($("#sentinel", root), load);
  onLeave(stopScroll);

  await load();
}

/** 一条评价（详情页与列表页共用）。 */
export function commentCard(c) {
  const pics = (c.picUrls || []).filter(Boolean);
  return `
    <div class="card pad" style="margin:10px 12px">
      <div style="display:flex;align-items:center;gap:10px">
        <img src="${esc(pic(c.avatar))}" alt="" style="width:34px;height:34px;border-radius:50%;background:var(--bg)" onerror="this.style.visibility='hidden'">
        <div style="flex:1;min-width:0">
          <div style="font-size:13px">${esc(maskName(c))}</div>
          <div style="font-size:12px;color:var(--price);letter-spacing:1px">${stars(c.star)}</div>
        </div>
        <span class="hint" style="padding:0">${esc(String(c.createTime || "").slice(0, 10))}</span>
      </div>
      ${c.content ? `<div style="margin-top:10px;font-size:14px;line-height:1.6;white-space:pre-wrap">${esc(c.content)}</div>` : ""}
      ${
        pics.length
          ? `<div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">${pics
              .map((u) => `<img src="${esc(pic(u))}" alt="" style="width:72px;height:72px;border-radius:8px;object-fit:cover;background:var(--bg)">`)
              .join("")}</div>`
          : ""
      }
      ${
        c.adminContent
          ? `<div style="margin-top:10px;padding:8px 10px;background:var(--bg);border-radius:8px;font-size:12px;color:var(--text-2)">
              店家回复：${esc(c.adminContent)}</div>`
          : ""
      }
    </div>`;
}

export function stars(n) {
  const value = Math.max(0, Math.min(5, Number(n) || 0));
  return "★".repeat(value) + "☆".repeat(5 - value);
}

/** 昵称打码：上游只给了 userId，用「用户1234」这种形式，别暴露完整 id。 */
function maskName(c) {
  if (c.username) return c.username;
  const id = String(c.userId || "");
  return id ? `用户${id.slice(-4)}` : "匿名用户";
}
