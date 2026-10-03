/**
 * 收货地址管理（`#/address`，可带 `?from=checkout`）。
 *
 * 用到的接口：
 *   GET    /address/list
 *   POST   /address            保存（**带 id 就是更新，不带就是新增**，见 AddressController）
 *   DELETE /address/{addressId}
 *
 * 结算页跳过来时（from=checkout），选中一条直接回结算页。
 */

import { api } from "../api.js";
import { $, confirmBox, emptyBox, esc, navBar, navEl, hideLoading, showLoading, skeletonList, toast } from "../ui.js";

export default async function addressPage(root, params, query) {
  const fromCheckout = query.get("from") === "checkout";
  navBar({ title: "收货地址", sub: true, right: `<button class="btn ghost sm" id="addBtn">新增</button>` });

  root.innerHTML = `<div id="box">${skeletonList(3)}</div>`;
  const box = $("#box", root);

  let list = [];

  async function load() {
    box.innerHTML = skeletonList(3);
    try {
      list = (await api("/address/list")) || [];
    } catch (e) {
      box.innerHTML = emptyBox(e.message);
      return;
    }
    render();
  }

  function render() {
    if (!list.length) {
      box.innerHTML = emptyBox("还没有收货地址", "新增地址", "#");
      box.querySelector("a.btn").onclick = (e) => {
        e.preventDefault();
        openForm(null);
      };
      return;
    }
    box.innerHTML = list
      .map(
        (a) => `
      <div class="card">
        <div class="address-card" data-id="${a.id}">
          <div style="flex:1">
            <div class="who">${esc(a.name)}<span>${esc(a.tel)}</span>
              ${a.isDefault ? '<span class="tag" style="margin-left:6px">默认</span>' : ""}
            </div>
            <div class="where">${esc([a.province, a.city, a.county, a.addressDetail].filter(Boolean).join(" "))}</div>
          </div>
        </div>
        <div class="bottom" style="display:flex;gap:8px;justify-content:flex-end;padding:0 14px 12px">
          <button class="btn sm" data-act="edit" data-id="${a.id}">编辑</button>
          <button class="btn sm danger" data-act="del" data-id="${a.id}">删除</button>
        </div>
      </div>`,
      )
      .join("");
  }

  box.addEventListener("click", async (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    const id = e.target.closest("[data-id]")?.dataset.id;

    if (act === "edit") return openForm(list.find((a) => String(a.id) === id));
    if (act === "del") {
      if (!(await confirmBox({ title: "删除这个地址？", okText: "删除", danger: true }))) return;
      showLoading("删除中…");
      try {
        await api(`/address/${id}`, { method: "DELETE" });
        toast("已删除");
        await load();
      } catch (ex) {
        toast(ex.message);
      } finally {
        hideLoading();
      }
      return;
    }

    // 点整张卡片：结算页跳来的就返回结算，否则进入编辑
    const card = e.target.closest(".address-card");
    if (card) {
      if (fromCheckout) location.hash = "#/checkout";
      else openForm(list.find((a) => String(a.id) === card.dataset.id));
    }
  });

  navEl("addBtn").onclick = () => openForm(null);

  /** 新增/编辑表单（同一个弹层，靠有没有 id 区分）。 */
  function openForm(address) {
    const a = address || {};
    const mask = document.createElement("div");
    mask.className = "mask";
    mask.innerHTML = `
      <div class="sheet">
        <div class="sheet-head"><h3>${address ? "编辑地址" : "新增地址"}</h3></div>
        <div class="sheet-body">
          <div class="field" style="padding:0">
            <label>收货人</label><input id="f-name" value="${esc(a.name)}" placeholder="姓名">
            <label>手机号</label><input id="f-tel" inputmode="numeric" maxlength="11" value="${esc(a.tel)}" placeholder="11 位手机号">
            <label>省 / 市 / 区</label>
            <div class="inline">
              <input id="f-province" value="${esc(a.province)}" placeholder="省">
              <input id="f-city" value="${esc(a.city)}" placeholder="市">
              <input id="f-county" value="${esc(a.county)}" placeholder="区/县">
            </div>
            <label>详细地址</label>
            <textarea id="f-detail" rows="2" placeholder="街道、门牌号等">${esc(a.addressDetail)}</textarea>
          </div>
          <label class="switch" style="padding:12px 0"><input type="checkbox" id="f-default" ${a.isDefault ? "checked" : ""}> 设为默认地址</label>
          <p class="hint" style="padding:0;color:var(--danger)" id="f-err"></p>
        </div>
        <div class="sheet-foot">
          <button class="btn block" data-close>取消</button>
          <button class="btn primary block" id="f-save">保存</button>
        </div>
      </div>`;

    mask.addEventListener("click", (e) => {
      if (e.target === mask || e.target.closest("[data-close]")) mask.remove();
    });

    mask.querySelector("#f-save").onclick = async () => {
      const body = {
        id: a.id ?? null,
        name: mask.querySelector("#f-name").value.trim(),
        tel: mask.querySelector("#f-tel").value.trim(),
        province: mask.querySelector("#f-province").value.trim(),
        city: mask.querySelector("#f-city").value.trim(),
        county: mask.querySelector("#f-county").value.trim(),
        addressDetail: mask.querySelector("#f-detail").value.trim(),
        isDefault: mask.querySelector("#f-default").checked,
      };
      const err = mask.querySelector("#f-err");
      if (!body.name) return (err.textContent = "请填写收货人");
      if (!/^1\d{10}$/.test(body.tel)) return (err.textContent = "请填写 11 位手机号");
      if (!body.province || !body.city) return (err.textContent = "请填写省市");
      if (!body.addressDetail) return (err.textContent = "请填写详细地址");

      const btn = mask.querySelector("#f-save");
      btn.disabled = true;
      try {
        await api("/address", { method: "POST", body });
        mask.remove();
        toast("已保存");
        await load();
      } catch (e) {
        err.textContent = e.message;
        btn.disabled = false;
      }
    };

    document.body.appendChild(mask);
  }

  await load();
}
