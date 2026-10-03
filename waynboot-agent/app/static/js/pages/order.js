/**
 * 订单详情（`#/order/订单号`）。
 *
 * 用到的接口：
 *   GET  /order/detail/{orderSn}
 *   POST /order/cancel/{id} · /order/confirm/{id} · /order/delete/{id} · /order/refund/{id}
 *
 * ⚠️ 详情接口**不返回 handleOption**（列表接口才有），所以底部按钮只能前端按状态推导。
 * 规则抄自服务端 `OrderUtil.build`（domain-trade），两边要一起改：
 *   101 未付款 → 取消/去支付 ｜ 102·103 已取消 → 删除 ｜ 201 已付款 → 申请退款
 *   203 已退款 → 删除 ｜ 301 已发货 → 确认收货 ｜ 401·402 已收货 → 删除
 * 真正的拦截仍在服务端（状态机 + 归属校验），这里只管显示。
 */

import { api, pic } from "../api.js";
import { $, confirmBox, emptyBox, esc, hideLoading, money, navBar, showLoading, skeletonList, toast } from "../ui.js";

export default async function orderDetailPage(root, params) {
  const orderSn = params.sn;
  navBar({ title: "订单详情", sub: true });

  root.innerHTML = `<div id="box">${skeletonList(4)}</div>`;
  const box = $("#box", root);

  let order;
  try {
    order = await api(`/order/detail/${orderSn}`);
  } catch (e) {
    box.innerHTML = emptyBox(e.message, "回到订单列表", "#/orders");
    return;
  }

  render();
  box.addEventListener("click", onClick);

  function render() {
    const can = actionsOf(Number(order.orderStatus));
    const canComment = Number(order.orderStatus) === 401 || Number(order.orderStatus) === 402;
    const goods = order.orderGoodsVOList || [];
    const stText = order.orderStatusText || "";
    const stClass = /未付款/.test(stText) ? "warn" : /退款|取消/.test(stText) ? "" : "ok";

    box.innerHTML = `
      <div class="card pad" style="text-align:center;padding:20px 14px">
        <div style="font-size:18px;font-weight:600" class="${stClass}">${esc(stText)}</div>
        ${order.aftersaleStatus ? `<div class="hint" style="padding:6px 0 0">售后状态：${esc(order.aftersaleStatus)}</div>` : ""}
      </div>

      <div class="card">
        <div class="address-card">
          <div style="flex:1">
            <div class="who">${esc(order.consignee || "-")}<span>${esc(order.mobile || "")}</span></div>
            <div class="where">${esc(order.address || "")}</div>
          </div>
        </div>
      </div>

      <div class="card">
        ${goods
          .map(
            (g) => `
          <div class="goods-line" data-goods="${g.goodsId}" style="cursor:pointer">
            <img src="${esc(pic(g.picUrl))}" alt="">
            <div class="info">
              <div class="name">${esc(g.goodsName)}</div>
              ${(g.specifications || []).filter(Boolean).length ? `<div class="spec">${esc(g.specifications.filter(Boolean).join(" / "))}</div>` : ""}
            </div>
            <div style="text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:6px">
              <div style="font-size:13px">¥${money(g.price)}</div>
              <div class="num">×${g.number}</div>
              ${
                // 已收货（或系统收货）才能评价；服务端会按 orderGoodsId 关联，一条商品行评一次
                canComment && !g.comment
                  ? `<button class="btn sm" data-comment="${g.id}" data-name="${esc(g.goodsName)}">评价</button>`
                  : g.comment
                    ? `<span class="tag gray">已评价</span>`
                    : ""
              }
            </div>
          </div>`,
          )
          .join("")}
      </div>

      <div class="card pad">
        <div class="money-row"><span>商品金额</span><span>¥${money(order.goodsPrice)}</span></div>
        <div class="money-row"><span>运费</span><span>¥${money(order.freightPrice)}</span></div>
        ${Number(order.couponPrice) ? `<div class="money-row"><span>优惠券</span><span style="color:var(--price)">-¥${money(order.couponPrice)}</span></div>` : ""}
        ${Number(order.refundAmount) ? `<div class="money-row"><span>退款金额</span><span>¥${money(order.refundAmount)}</span></div>` : ""}
        <div class="money-row total"><span>实付</span><b>¥${money(order.actualPrice)}</b></div>
      </div>

      <div class="card">
        ${infoRow("订单编号", order.orderSn)}
        ${infoRow("下单时间", order.createTime)}
        ${order.payTime ? infoRow("支付时间", order.payTime) : ""}
        ${order.payId ? infoRow("支付流水", order.payId) : ""}
        ${order.shipTime ? infoRow("发货时间", order.shipTime) : ""}
        ${order.shipSn ? infoRow("物流单号", order.shipSn) : ""}
        ${order.shipChannel ? infoRow("物流公司", order.shipChannel) : ""}
        ${order.refundTime ? infoRow("退款时间", order.refundTime) : ""}
        ${order.message ? infoRow("买家备注", order.message) : ""}
      </div>

      <div style="height:70px"></div>
      ${
        can.length
          ? `<div class="detail-bar" style="justify-content:flex-end">
              ${can
                .map((a) => `<button class="btn ${a.primary ? "primary" : ""} ${a.danger ? "danger" : ""}" data-act="${a.act}">${esc(a.label)}</button>`)
                .join("")}
            </div>`
          : ""
      }`;
  }

  function infoRow(label, value) {
    return `<div class="cell" style="cursor:default">
      <div class="cell-label" style="color:var(--muted);font-size:13px">${esc(label)}</div>
      <div class="cell-value">${esc(value)}</div>
    </div>`;
  }

  async function onClick(e) {
    const commentBtn = e.target.closest("[data-comment]");
    if (commentBtn) {
      openCommentSheet(commentBtn.dataset.comment, commentBtn.dataset.name);
      return;
    }
    const goodsLine = e.target.closest("[data-goods]");
    if (goodsLine && !e.target.closest("[data-act]")) {
      location.hash = `#/goods/${goodsLine.dataset.goods}`;
      return;
    }

    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;

    if (act === "pay") return (location.hash = `#/pay/${orderSn}`);
    if (act === "cancel") {
      if (!(await confirmBox({ title: "取消这笔订单？", text: "取消后库存会释放，订单不可恢复", okText: "取消订单", danger: true }))) return;
      return run(`/order/cancel/${order.id}`, "订单已取消");
    }
    if (act === "confirm") {
      if (!(await confirmBox({ title: "确认已收到货？", okText: "确认收货" }))) return;
      return run(`/order/confirm/${order.id}`, "已确认收货");
    }
    if (act === "refund") {
      if (!(await confirmBox({ title: "申请退款？", text: "退款需平台审核，审核通过后原路退回", okText: "申请退款" }))) return;
      return run(`/order/refund/${order.id}`, "退款申请已提交");
    }
    if (act === "delete") {
      if (!(await confirmBox({ title: "删除这笔订单？", okText: "删除", danger: true }))) return;
      showLoading("处理中…");
      try {
        await api(`/order/delete/${order.id}`, { method: "POST" });
        toast("订单已删除");
        location.hash = "#/orders";
      } catch (e2) {
        toast(e2.message);
      } finally {
        hideLoading();
      }
    }
  }

  /** 发表评价（星级 + 文字）。图片上传上游没给移动端接口，所以只做文字评价。 */
  function openCommentSheet(orderGoodsId, goodsName) {
    let star = 5;
    const mask = document.createElement("div");
    mask.className = "mask";
    mask.innerHTML = `
      <div class="sheet">
        <div class="sheet-head"><h3>评价商品</h3></div>
        <div class="sheet-body">
          <div style="font-size:13px;color:var(--text-2);margin-bottom:10px">${esc(goodsName || "")}</div>
          <div id="starRow" style="font-size:30px;color:var(--price);letter-spacing:6px;cursor:pointer;user-select:none">★★★★★</div>
          <textarea id="cContent" rows="3" placeholder="说说使用感受（选填）"
                    style="width:100%;margin-top:12px;padding:10px;border:1px solid var(--line-strong);border-radius:10px"></textarea>
          <p class="hint" style="padding:8px 0 0;color:var(--danger)" id="cErr"></p>
        </div>
        <div class="sheet-foot">
          <button class="btn block" data-close>取消</button>
          <button class="btn primary block" id="cSubmit">发布</button>
        </div>
      </div>`;

    const starRow = mask.querySelector("#starRow");
    const paint = () => (starRow.textContent = "★".repeat(star) + "☆".repeat(5 - star));
    starRow.onclick = (ev) => {
      // 点第 N 颗星就是 N 分
      const rect = starRow.getBoundingClientRect();
      const idx = Math.ceil(((ev.clientX - rect.left) / rect.width) * 5);
      star = Math.max(1, Math.min(5, idx));
      paint();
    };

    mask.addEventListener("click", (e) => {
      if (e.target === mask || e.target.closest("[data-close]")) mask.remove();
    });

    mask.querySelector("#cSubmit").onclick = async () => {
      const btn = mask.querySelector("#cSubmit");
      btn.disabled = true;
      try {
        await api("/comment", {
          method: "POST",
          body: {
            orderGoodsId: Number(orderGoodsId),
            valueId: goodsLineValueId(orderGoodsId),
            type: 0,
            star,
            content: mask.querySelector("#cContent").value.trim(),
            // 服务端会读 picUrls.length，不给会 NPE —— 图片上传没有移动端接口，固定空数组
            picUrls: [],
          },
        });
        mask.remove();
        toast("评价已发布");
        order = await api(`/order/detail/${orderSn}`);
        render();
      } catch (e) {
        mask.querySelector("#cErr").textContent = e.message;
        btn.disabled = false;
      }
    };

    document.body.appendChild(mask);
    paint();
  }

  /** 从订单行里取出 goodsId（评价接口要 valueId = 商品 id）。 */
  function goodsLineValueId(orderGoodsId) {
    const line = (order.orderGoodsVOList || []).find((g) => String(g.id) === String(orderGoodsId));
    return line ? line.goodsId : undefined;
  }

  async function run(path, successText) {
    showLoading("处理中…");
    try {
      await api(path, { method: "POST" });
      toast(successText);
      order = await api(`/order/detail/${orderSn}`);
      render();
    } catch (e) {
      toast(e.message);
    } finally {
      hideLoading();
    }
  }
}

/** 状态 → 可执行动作（抄自 OrderUtil.build）。 */
function actionsOf(status) {
  if (status === 101) {
    return [
      { act: "cancel", label: "取消订单" },
      { act: "pay", label: "去支付", primary: true },
    ];
  }
  if (status === 102 || status === 103) return [{ act: "delete", label: "删除订单", danger: true }];
  if (status === 201) return [{ act: "refund", label: "申请退款" }];
  if (status === 202 || status === 204) return [];
  if (status === 203) return [{ act: "delete", label: "删除订单", danger: true }];
  if (status === 301) return [{ act: "confirm", label: "确认收货", primary: true }];
  if (status === 401 || status === 402) return [{ act: "delete", label: "删除订单", danger: true }];
  return [];
}
