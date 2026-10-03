/**
 * 秒杀详情 + 抢购（`#/seckill/活动id`）。
 *
 * 用到的接口：
 *   GET  /seckill/detail/{activityId}   活动 + SKU 列表（含秒杀价、活动库存、限购）
 *   GET  /seckill/token/{activitySkuId} 拿**短期 token**（秒杀漏斗的第一道门）
 *   POST /seckill/submit                提交（异步落单：outbox → MQ → 消费端）
 *   GET  /seckill/result/{orderSn}      轮询下单结果
 *   GET  /address/list                  收货地址
 *
 * 抢购顺序不能变：**先拿 token 再提交**——服务端校验 token 有效性与归属，
 * 这是"提前抢跑/脚本刷单"的第一道拦截（见 SeckillOrderSubmitSupport）。
 * token 有有效期，所以要在拿到之后尽快提交。
 */

import { api, pic, requireLogin } from "../api.js";
import { $, $$, esc, hideLoading, money, navBar, showLoading, skeletonList, toast } from "../ui.js";

export default async function seckillDetailPage(root, params) {
  const activityId = params.id;
  navBar({ title: "秒杀详情", sub: true });

  root.innerHTML = `<div id="box">${skeletonList(3)}</div>`;
  const box = $("#box", root);

  let data;
  try {
    data = await api(`/seckill/detail/${activityId}`);
  } catch (e) {
    box.innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
    return;
  }

  const activity = data?.activity || {};
  const skus = data?.skuList || [];
  let chosen = skus[0] || null;
  let buying = false;

  render();
  box.addEventListener("click", onClick);

  function render() {
    const now = Date.now();
    const start = new Date(activity.startTime).getTime();
    const end = new Date(activity.endTime).getTime();
    const phase = activity.status !== 1 ? "已下线" : now < start ? "未开始" : now > end ? "已结束" : "抢购中";
    const open = phase === "抢购中";

    box.innerHTML = `
      <div class="card pad">
        <div style="font-size:17px;font-weight:600">${esc(activity.name || "")}</div>
        <div class="hint" style="padding:6px 0 0">${esc(activity.brief || "")}</div>
        <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
          <span class="tag ${open ? "hot" : "gray"}">${esc(phase)}</span>
          <span class="hint" style="padding:0">${esc(fmt(activity.startTime))} ~ ${esc(fmt(activity.endTime))}</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">选择商品</div>
        ${skus
          .map(
            (s) => `
          <div class="goods-line" data-sku="${s.id}" style="cursor:pointer;${s.id === chosen?.id ? "background:var(--brand-soft)" : ""}">
            <img src="${esc(pic(s.picUrl || s.goodsPicUrl))}" alt="">
            <div class="info">
              <div class="name">${esc(s.goodsName || "")}</div>
              ${(s.specifications || []).filter(Boolean).length ? `<div class="spec">${esc(s.specifications.filter(Boolean).join(" / "))}</div>` : ""}
              <div class="spec">活动库存 ${s.availableStock} · 每人限购 ${s.limitCount} 件</div>
            </div>
            <div style="text-align:right">
              <div class="price"><span class="sym">¥</span>${money(s.seckillPrice)}</div>
            </div>
          </div>`,
          )
          .join("")}
      </div>

      <div class="card pad">
        <div class="hint" style="padding:0">
          秒杀价与库存由活动决定；提交后走消息队列异步落单，通常一两秒出结果。
          每人限购 ${chosen?.limitCount ?? "-"} 件，抢购前需要先拿一次性令牌（服务端防刷的第一道门）。
        </div>
      </div>

      <div style="height:70px"></div>
      <div class="detail-bar">
        <div style="flex:1;font-size:13px;color:var(--text-2)">
          ${chosen ? `秒杀价 <b style="color:var(--price);font-size:18px">¥${money(chosen.seckillPrice)}</b>` : "请选择商品"}
        </div>
        <button class="btn primary lg" id="buy" style="flex:none;padding:0 26px" ${open && chosen && !buying ? "" : "disabled"}>
          ${buying ? "抢购中…" : open ? "立即抢购" : phase}
        </button>
      </div>`;
  }

  function fmt(value) {
    return String(value || "").replace("T", " ").slice(5, 16);
  }

  async function onClick(e) {
    const line = e.target.closest("[data-sku]");
    if (line) {
      chosen = skus.find((s) => String(s.id) === line.dataset.sku) || chosen;
      render();
      return;
    }
    if (e.target.closest("#buy")) await buy();
  }

  async function buy() {
    if (!requireLogin()) return;
    if (!chosen || buying) return;
    buying = true;
    render();
    showLoading("正在抢购…");

    try {
      // 1) 收货地址（秒杀也要填地址，和普通下单一样）
      const addresses = (await api("/address/list")) || [];
      if (!addresses.length) {
        hideLoading();
        buying = false;
        render();
        toast("请先添加收货地址");
        location.hash = "#/address?from=checkout";
        return;
      }
      const address = addresses.find((a) => a.isDefault) || addresses[0];

      // 2) 短期 token —— 必须在提交前拿，且有有效期
      const tokenRes = await api(`/seckill/token/${chosen.id}`);
      const seckillToken = tokenRes?.token;
      if (!seckillToken) throw new Error("没有拿到抢购令牌，请重试");

      // 3) 提交（异步落单）
      const submit = await api("/seckill/submit", {
        method: "POST",
        body: { activitySkuId: chosen.id, number: 1, addressId: address.id, seckillToken, message: "" },
      });
      const orderSn = submit?.orderSn;
      if (!orderSn) throw new Error("下单没有返回订单号");

      // 4) 轮询结果（与普通下单同一个套路：宽限期内失败=处理中）
      const ok = await waitResult(orderSn);
      hideLoading();
      buying = false;
      if (ok) {
        toast("抢购成功");
        location.hash = `#/order/${orderSn}`;
      } else {
        render();
        toast("没抢到，可能已经卖完了");
      }
    } catch (e) {
      hideLoading();
      buying = false;
      render();
      toast(e.message || "抢购失败");
    }
  }

  /** 轮询秒杀结果。上游"还没处理完"和"真的失败"都是失败文案，所以用时间兜住。 */
  async function waitResult(orderSn, timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        await api(`/seckill/result/${orderSn}`);
        return true; // 返回 200 即成功
      } catch {
        await new Promise((r) => setTimeout(r, 1200));
      }
    }
    return false;
  }
}
