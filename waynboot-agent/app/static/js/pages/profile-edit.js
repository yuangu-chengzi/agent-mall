/**
 * 编辑资料（`#/profile/edit`）：昵称 / 性别 / 生日 / 邮箱 / 头像 / 改密码。
 *
 * 用到的接口：
 *   POST /user/profile        改资料（只提交非空的字段，服务端也是"填了才更新"）
 *   POST /user/uploadAvatar   **参数是已经上传好的图片地址字符串**（`avatar`），不是文件流 ——
 *                             移动端没有通用的文件上传接口，所以这里让用户从预设头像里挑一个，
 *                             走的是同一个接口，语义也说得通
 *   POST /user/updatePassword {oldPassword, password, confirmPassword}
 *
 * ⚠️ 验证码登录的用户，密码是常量默认值（`SysConstants.DEFAULT_PASSWORD = 123456`），
 * 所以"原密码"要填 123456。页面上直接把这个提示写出来，免得用户猜。
 */

import { api, pic } from "../api.js";
import { clearUser, getUser } from "../store.js";
import { $, confirmBox, esc, hideLoading, navBar, showLoading, skeletonList, toast } from "../ui.js";

//: 预设头像（由 scripts/gen_demo_images.py 一并生成到演示图片目录）
const PRESET_AVATARS = Array.from({ length: 6 }, (_, i) => `/upload/demo/avatar-${i + 1}.webp`);

export default async function profileEditPage(root) {
  navBar({ title: "编辑资料", sub: true });

  root.innerHTML = `<div id="box">${skeletonList(4)}</div>`;
  const box = $("#box", root);

  let user;
  try {
    user = await getUser(true);
  } catch (e) {
    box.innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
    return;
  }

  let avatar = user.avatar || "";

  box.innerHTML = `
    <div class="card">
      <div class="cell" style="cursor:default">
        <div class="cell-label">头像</div>
        <img id="avatarNow" src="${esc(pic(avatar))}" alt=""
             style="width:52px;height:52px;border-radius:50%;object-fit:cover;background:var(--bg)">
      </div>
      <div style="padding:4px 14px 14px">
        <div style="display:flex;gap:10px;flex-wrap:wrap" id="avatarRow">
          ${PRESET_AVATARS.map(
            (u, i) => `<img src="${esc(pic(u))}" data-avatar="${esc(u)}" alt=""
              style="width:48px;height:48px;border-radius:50%;object-fit:cover;cursor:pointer;border:2px solid ${u === avatar ? "var(--brand)" : "transparent"}"
              title="预设头像 ${i + 1}">`,
          ).join("")}
        </div>
        <p class="hint" style="padding:8px 0 0">选一个预设头像（移动端上传接口只收图片地址，没有文件上传）</p>
      </div>
    </div>

    <div class="card">
      <div class="field" style="padding:6px 14px 14px">
        <label>昵称</label>
        <input id="f-nickname" value="${esc(user.nickname || "")}" placeholder="给自己起个名字">
        <label>性别</label>
        <select id="f-gender">
          <option value="">不填</option>
          <option value="0" ${user.gender === 0 ? "selected" : ""}>保密</option>
          <option value="1" ${user.gender === 1 ? "selected" : ""}>男</option>
          <option value="2" ${user.gender === 2 ? "selected" : ""}>女</option>
        </select>
      </div>
    </div>

    <div style="padding:6px 12px 0">
      <button class="btn primary block lg" id="save">保存资料</button>
    </div>

    <div class="card" style="margin-top:16px">
      <div class="card-title">修改密码</div>
      <div class="field" style="padding:6px 14px 14px">
        <label>原密码</label>
        <input id="p-old" type="password" placeholder="验证码登录的用户默认是 123456">
        <label>新密码</label>
        <input id="p-new" type="password" placeholder="至少 6 位">
        <label>确认新密码</label>
        <input id="p-confirm" type="password">
        <p class="hint" style="padding:8px 0 0;color:var(--danger)" id="p-err"></p>
      </div>
      <div style="padding:0 14px 14px">
        <button class="btn block" id="changePwd">修改密码</button>
      </div>
    </div>

    <div style="padding:8px 12px 24px">
      <button class="btn block danger" id="logout">退出登录</button>
    </div>`;

  $("#avatarRow", box).addEventListener("click", (e) => {
    const el = e.target.closest("[data-avatar]");
    if (!el) return;
    avatar = el.dataset.avatar;
    $("#avatarNow", box).src = pic(avatar);
    [...box.querySelectorAll("[data-avatar]")].forEach((n) =>
      n.style.setProperty("border-color", n === el ? "var(--brand)" : "transparent"),
    );
  });

  $("#save", box).onclick = async () => {
    const genderText = $("#f-gender", box).value;
    // 只提交"页面能显示出来"的字段：邮箱/生日虽然接口收，但 /user/info 不返回，
    // 填了下次进来还是空的，反而让人以为没保存成功，所以不做
    const body = {
      nickname: $("#f-nickname", box).value.trim(),
      gender: genderText === "" ? undefined : Number(genderText),
    };
    const hasAvatarChange = avatar && avatar !== user.avatar;

    showLoading("保存中…");
    try {
      await api("/user/profile", { method: "POST", body });
      if (hasAvatarChange) {
        // 顺序：先改资料再换头像（两个接口各自更新一次 member）
        await api("/user/uploadAvatar", { method: "POST", query: { avatar } });
        user.avatar = avatar;
      }
      clearUser();
      await getUser(true);
      toast("资料已保存");
    } catch (e) {
      toast(e.message);
    } finally {
      hideLoading();
    }
  };

  $("#changePwd", box).onclick = async () => {
    const err = $("#p-err", box);
    const oldPassword = $("#p-old", box).value.trim();
    const password = $("#p-new", box).value.trim();
    const confirmPassword = $("#p-confirm", box).value.trim();
    if (!oldPassword) return (err.textContent = "请填原密码（验证码登录默认 123456）");
    if (password.length < 6) return (err.textContent = "新密码至少 6 位");
    if (password !== confirmPassword) return (err.textContent = "两次输入的新密码不一致");
    err.textContent = "";

    if (!(await confirmBox({ title: "确认修改密码？", text: "改完下次登录要用新密码", okText: "修改" }))) return;
    showLoading("提交中…");
    try {
      await api("/user/updatePassword", { method: "POST", body: { oldPassword, password, confirmPassword } });
      toast("密码已修改");
      $("#p-old", box).value = $("#p-new", box).value = $("#p-confirm", box).value = "";
    } catch (e) {
      err.textContent = e.message;
    } finally {
      hideLoading();
    }
  };

  $("#logout", box).onclick = async () => {
    if (!(await confirmBox({ title: "退出登录？", okText: "退出" }))) return;
    try {
      await api("/logout", { method: "POST" });
    } catch {
      /* 服务端失败也要清本地，否则卡在登录态 */
    }
    localStorage.clear();
    clearUser();
    location.hash = "#/login";
  };
}
