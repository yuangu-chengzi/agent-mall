/**
 * 购物车页。
 *
 * 用到的接口：
 *   GET    /cart/list                  列表
 *   PUT    /cart                       勾选/取消勾选  {id, checked}
 *   POST   /cart/changeNum/{id}/{num}  改数量
 *   DELETE /cart/{id}                  删除
 *
 * 结算不是"把选中项传过去"，而是**后端按购物车里的 checked 标记汇总**（见结算页），
 * 所以这里改勾选状态必须真的落库，不能只改前端。
 */

import { api, pic } from "../api.js";
import { refreshCartCount } from "../store.js";
import { $, confirmBox, emptyBox, esc, money, navBar, navEl, priceHtml, showLoading, hideLoading, skeletonList, toast } from "../ui.js";

export default async function cartPage(root) {
  navBar({ title: "购物车", sub: false, right: `<button class="btn ghost sm" id="editBtn">管理</button>` });

  root.innerHTML = `<div id="listBox">${skeletonList(3)}</div><div id="bar"></div>`;
  let items = [];
  let editing = false;

  navEl("editBtn").onclick = () => {
    editing = !editing;
    navEl("editBtn").textContent = editing ? "完成" : "管理";
    render();
  };

  async function load() {
    try {
      items = (await api("/cart/list", { query: { pageNum: 1, pageSize: 50 } })) || [];
    } catch (e) {
      $("#listBox", root).innerHTML = emptyBox(e.message);
      return;
    }
    render();
    refreshCartCount();
  }

  function checkedItems() {
    return items.filter((i) => i.checked);
  }

  function render() {
    const box = $("#listBox", root);
    if (!items.length) {
      box.innerHTML = emptyBox("购物车还是空的", "去逛逛", "#/home");
      $("#bar", root).innerHTML = "";
      return;
    }

    box.innerHTML = `<div class="card flat">${items.map(rowHtml).join("")}</div>`;

    const checked = checkedItems();
    const total = checked.reduce((sum, i) => sum + Number(i.price || 0) * Number(i.number || 0), 0);
    const allChecked = checked.length === items.length;

    $("#bar", root).innerHTML = `
      <div class="checkout-bar">
        <label class="switch" style="padding:0;gap:6px">
          <input type="checkbox" id="allCheck" ${allChecked ? "checked" : ""}>
          <span style="font-size:13px">全选</span>
        </label>
        <div class="sum">
          ${editing ? `已选 ${checked.length} 件` : `合计 <b>¥${money(total)}</b>`}
        </div>
        ${
          editing
            ? `<button class="btn danger" id="batchDel">删除所选</button>`
            : `<button class="btn primary" id="toCheckout" ${checked.length ? "" : "disabled"}>去结算(${checked.length})</button>`
        }
      </div>`;

    $("#allCheck", root).onchange = async (e) => {
      const next = e.target.checked;
      showLoading("处理中…");
      try {
        await Promise.all(
          items.filter((i) => Boolean(i.checked) !== next).map((i) => api("/cart", { method: "PUT", body: { id: i.id, checked: next } })),
        );
        items.forEach((i) => (i.checked = next));
        render();
      } catch (ex) {
        toast(ex.message);
      } finally {
        hideLoading();
      }
    };

    $("#toCheckout", root)?.addEventListener("click", () => (location.hash = "#/checkout"));

    $("#batchDel", root)?.addEventListener("click", async () => {
      const chosen = checkedItems();
      if (!chosen.length) return toast("请先勾选要删除的商品");
      if (!(await confirmBox({ title: `删除这 ${chosen.length} 件商品？`, okText: "删除", danger: true }))) return;
      showLoading("删除中…");
      try {
        await Promise.all(chosen.map((i) => api(`/cart/${i.id}`, { method: "DELETE" })));
        toast("已删除");
        await load();
      } catch (e) {
        toast(e.message);
      } finally {
        hideLoading();
      }
    });
  }

  function rowHtml(item) {
    const spec = (item.specifications || []).filter(Boolean).join(" / ");
    return `
      <div class="cart-item" data-id="${item.id}">
        <label class="pick">
          <input type="checkbox" data-act="check" ${item.checked ? "checked" : ""}>
        </label>
        <img src="${esc(pic(item.picUrl))}" alt="" data-act="open" data-goods="${item.goodsId}">
        <div class="info">
          <div class="name" data-act="open" data-goods="${item.goodsId}">${esc(item.goodsName)}</div>
          ${spec ? `<div class="spec">${esc(spec)}</div>` : ""}
          <div class="foot">
            ${priceHtml(item.price)}
            <span class="stepper">
              <button data-act="minus" ${item.number <= 1 ? "disabled" : ""}>−</button>
              <span>${item.number}</span>
              <button data-act="plus" ${item.maxNum && item.number >= item.maxNum ? "disabled" : ""}>＋</button>
            </span>
          </div>
        </div>
        <button class="del" data-act="del">删除</button>
      </div>`;
  }

  // 事件委托：列表整体重绘也不需要重新绑
  $("#listBox", root).addEventListener("click", async (e) => {
    const row = e.target.closest(".cart-item");
    if (!row) return;
    const item = items.find((i) => String(i.id) === row.dataset.id);
    if (!item) return;
    const act = e.target.dataset.act;

    try {
      if (act === "open") {
        location.hash = `#/goods/${e.target.dataset.goods}`;
      } else if (act === "check") {
        const next = e.target.checked;
        await api("/cart", { method: "PUT", body: { id: item.id, checked: next } });
        item.checked = next;
        render();
      } else if (act === "minus" || act === "plus") {
        const next = item.number + (act === "plus" ? 1 : -1);
        if (next < 1) return;
        await api(`/cart/changeNum/${item.id}/${next}`, { method: "POST" });
        item.number = next;
        render();
      } else if (act === "del") {
        if (!(await confirmBox({ title: "把这个商品移出购物车？", okText: "删除", danger: true }))) return;
        await api(`/cart/${item.id}`, { method: "DELETE" });
        await load();
      }
    } catch (ex) {
      toast(ex.message);
    }
  });

  await load();
}
