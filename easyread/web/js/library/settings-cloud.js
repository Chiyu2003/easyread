/* 设置 → 云文献库：只管理本地位置，同步交给网盘客户端。 */
(function (PR) {
  "use strict";
  let busy = false, dismissedHost = "";
  const state = () => PR.settingsState;
  const redraw = () => { if (state().tab === "cloud") PR.settingsRender(); };
  const size = (n) => n >= 1073741824 ? (n / 1073741824).toFixed(1) + " GB" : (n / 1048576).toFixed(1) + " MB";
  const disabled = (s) => busy || (s.cloud && (s.cloud.temp || s.cloud.restart_required || s.cloud.moving));

  async function load(s) {
    if (s.cloudLoading) return;
    s.cloudLoading = true; s.cloudError = "";
    try { [s.cloud, s.drive] = await Promise.all([PR.api("/api/library/location"), PR.api("/api/drive/status")]); }
    catch (e) { s.cloudError = e.message; }
    finally { s.cloudLoading = false; redraw(); }
  }

  function button(action, label, path, off, primary) {
    return '<button class="btn sm ' + (primary ? "accent" : "line") + '" data-cloud="' + action + '"' + (path ? ' data-path="' + PR.esc(path) + '"' : "") +
      (off ? " disabled" : "") + ">" + PR.esc(label) + "</button>";
  }

  /* 一张卡片：图标 + 名字 + 路径 + 一句状态，右边是操作按钮 */
  function card(icon, label, name, path, stateText, acts, cls) {
    return '<div class="cloud-card' + (cls ? " " + cls : "") + '"><div class="cloud-card-icon">' + PR.icon(icon) + '</div><div class="cloud-card-body">' +
      (label ? '<span class="cloud-label">' + PR.esc(label) + "</span>" : "") + "<b>" + PR.esc(name) + "</b>" +
      (path ? '<div class="cloud-path">' + PR.esc(path) + "</div>" : "") + (stateText ? '<span class="cloud-state">' + PR.esc(stateText) + "</span>" : "") +
      '</div><div class="cloud-card-acts">' + acts + "</div></div>";
  }

  /* 网盘卡片：空的就“迁移到这里”；已有论文就“切换到这里”，合并是第二个按钮；自选的文件夹可以移除 */
  function target(s, item) {
    const name = item.label || PR.t("自选文件夹");
    if (item.incomplete) return card("cloud", "", name, item.path, PR.t("上次迁移到这里没有完成，原文献库不受影响"), button("cleanup", PR.t("清理未完成的迁移"), item.path, busy), "warn");
    const using = item.path === s.cloud.path, off = disabled(s) || !item.writable || using;
    const stateText = using ? PR.t("正在用") : !item.writable ? PR.t("不可写") : item.papers ? PR.t("里面已有 {n} 篇", { n: item.papers }) : PR.t("空文件夹");
    const acts = using ? "" : item.papers
      ? button("use", PR.t("切换"), item.path, off, true) + button("merge", PR.t("合并"), item.path, off)
      : button("copy", PR.t("迁移"), item.path, off, true);
    const forget = item.custom ? '<button class="cloud-x" data-cloud="forget" title="' + PR.t("移除") + '" aria-label="' + PR.t("移除") + '">' + PR.icon("x", "sm") + "</button>" : "";
    return card(item.label ? "cloud" : "folder", "", name, item.path, stateText, acts + forget);
  }

  function cloudOf(d) {  // 现在的文献库在哪个网盘里
    const norm = (p) => String(p || "").replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase() + "/";
    return (d.candidates || []).find((c) => c.root_path && norm(d.path).startsWith(norm(c.root_path)));
  }

  function result(s) {
    const r = s.cloudResult;
    if (!r && !(s.cloud && s.cloud.restart_required)) return "";
    return '<div class="cloud-result" role="status"><b>' + PR.t("需要重启 EasyRead 才能生效") + '</b><p class="cloud-path">' +
      PR.esc(r ? r.message : PR.t("文献库位置已更改，请先重启再继续阅读。")) + "</p>" +
      (r ? '<p>' + PR.t("已复制 {copied} 篇，跳过 {skipped} 项", { copied: r.copied || 0, skipped: (r.skipped || []).length }) + "</p>" +
        ((r.skipped || []).length ? '<details><summary>' + PR.t("查看跳过的项目") + '</summary><ul>' +
          r.skipped.map((x) => '<li class="cloud-path">' + PR.esc(x.id + ": " + x.reason) + '</li>').join("") + '</ul></details>' : "") : "") +
      (r && r.old_path ? '<p class="hint cloud-path">' + PR.esc(PR.t("原文献库保留在：{path}", { path: r.old_path })) + "</p>" : "") +
      (window.easyreadDesktop && window.easyreadDesktop.relaunch ? button("restart", PR.t("现在重启"), "", busy)
        : '<p class="hint">' + PR.t("请关掉 EasyRead 再重新打开") + "</p>") + "</div>";
  }

  function driveSection(s) {
    const d = s.drive || {};
    const blocked = d.temporary_library;
    const connect = d.connected ? button("google-sync", busy ? PR.t("正在同步…") : PR.t("立即同步"), "", busy || blocked, true)
      : button("google-connect", PR.t("连接 Google Drive"), "", busy || blocked || !d.credential_configured, true);
    return '<h4 class="set-h">Google Drive</h4><div class="cloud-card"><div class="cloud-card-body"><b>' +
      PR.esc(d.connected ? PR.t("已连接") : PR.t("未连接")) + '</b><span class="cloud-state">' +
      PR.esc(d.connected ? (d.last_sync ? PR.t("上次同步：{time}", { time: d.last_sync }) : PR.t("首次同步会先复制本机文献库")) :
        d.credential_configured ? PR.t("授权后可把文献库同步到 EasyRead 专用 Drive 文件夹") : PR.t("先设置 Google Desktop OAuth 客户端文件")) + '</span>' +
      (!d.credential_configured && !d.connected ? '<div class="cloud-path">' + PR.esc(d.credential_path || "") + '</div>' : "") +
      (s.driveResult ? '<span class="cloud-state">' + PR.esc(s.driveResult.message || "") + (s.driveResult.conflicts && s.driveResult.conflicts.length ? ' · ' + PR.t("冲突版本：{n}", { n: s.driveResult.conflicts.length }) : "") + '</span>' : "") +
      '</div><div class="cloud-card-acts">' + connect + '</div></div>' +
      (blocked ? '<p class="hint">' + PR.t("临时文献库不能连接 Google Drive 或同步实验任务。") + '</p>' :
        !d.credential_configured && !d.connected ? '<p class="hint">' + PR.t("在 Google Cloud 创建 Desktop app OAuth client，将下载的 JSON 保存到上方路径；授权范围使用 drive.file。") + '</p>' : "");
  }

  PR.settingsTabs.cloud = {
    render(s) {
      if (!s.cloud && !s.cloudLoading && !s.cloudError) load(s);
      const d = s.cloud;
      if (!d) return '<div class="cloud-settings">' + (s.cloudError ? '<p class="bad">' + PR.esc(s.cloudError) + "</p>" : "") +
        '<p class="hint">' + (s.cloudLoading ? PR.t("正在检查文献库位置…") : "") + "</p>" + button("refresh", PR.t("重试"), "", s.cloudLoading) + "</div>";
      const inCloud = cloudOf(d), others = (d.candidates || []).filter((c) => c.path !== d.path);
      return '<div class="cloud-settings">' +
        driveSection(s) +
        '<p class="set-lead">' + PR.t("网盘的同步文件夹也在电脑上，网盘客户端会把里面的文件自动传到云端。把文献库迁移到那里，换台电脑登录同一个网盘，论文、译文、笔记都还在。") + "</p>" +
        (s.cloudError ? '<p class="bad">' + PR.esc(s.cloudError) + "</p>" : "") + result(s) +
        '<h4 class="set-h">' + PR.t("本机") + "</h4>" +
        card(inCloud ? "cloud" : "folder", "", inCloud ? PR.t("在 {name} 里，会自动同步", { name: inCloud.label }) : PR.t("在本机，不会同步"), d.path,
          d.temp ? PR.t("当前是临时文献库，不能更改位置。") : "", button("reveal", PR.t("打开文件夹"), "", busy) + button("custom", PR.t("迁移…"), "", disabled(s), true)) +
        '<div class="cloud-head"><h4 class="set-h">' + PR.t("网盘") + '</h4><button class="linkish" data-cloud="refresh"' + (busy || d.restart_required ? " disabled" : "") + ">" + PR.t("重新检测") + "</button></div>" +
        '<div class="cloud-cards">' + others.map((c) => target(s, c)).join("") + (s.cloudCustom && s.cloudCustom.path !== d.path ? target(s, Object.assign({ custom: true }, s.cloudCustom)) : "") +
        card("plus", "", PR.t("其他网盘"), "", (d.candidates || []).length ? PR.t("坚果云、Google Drive 等：选它们的同步文件夹") : PR.t("没检测到 OneDrive、Dropbox、iCloud。坚果云、Google Drive 等：选它们的同步文件夹"),
          button("custom", PR.t("选择文件夹…"), "", disabled(s)), "ghost") + "</div>" +
        (busy ? '<p class="cloud-progress" role="status"><span class="spin"></span> ' + PR.t("正在处理文献库，请不要关闭 EasyRead…") + "</p>" : "") +
        '<h4 class="set-h">' + PR.t("注意") + '</h4><ul class="cloud-notes"><li>' +
        PR.t("迁移是复制，原来的文件夹保留不删。") + "</li><li>" +
        PR.t("不要在两台电脑上同时开着 EasyRead，否则同一篇论文的改动会互相覆盖。") + "</li><li>" +
        PR.t("iCloud 和 Google Drive 的省空间模式可能让文件只留在云端。请把 EasyRead 文件夹设为始终保留在此设备上。") + "</li><li>" +
        PR.t("百度网盘、迅雷没有实时同步文件夹，不适合放文献库。") + "</li></ul></div>";
    },
    async click(e, s) {
      const b = e.target.closest("[data-cloud]");
      if (!b) return false;
      const action = b.dataset.cloud;
      if (busy || b.disabled) return true;
      try {
        if (action === "google-connect") {
          const popup = window.open("about:blank", "easyread-google-oauth", "popup,width=620,height=760");
          const r = await PR.api("/api/drive/connect", { method: "POST", body: {} });
          if (popup) popup.location = r.auth_url; else location.href = r.auth_url;
          for (let i = 0; i < 180; i++) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            s.drive = await PR.api("/api/drive/status");
            if (s.drive.connected) { if (popup && !popup.closed) popup.close(); await load(s); return true; }
            if (s.drive.error) { if (popup && !popup.closed) popup.close(); throw new Error(s.drive.error); }
          }
          return true;
        }
        if (action === "google-sync") {
          if (!s.drive.last_sync && !(await PR.confirm({ title: PR.t("首次同步文献库"),
            body: PR.t("EasyRead 会把当前文献库复制到专用 Google Drive 文件夹。冲突版本会保留；请核对同步结果后，再在其他设备使用。"),
            ok: PR.t("开始同步") }))) return true;
          busy = true; redraw();
          s.driveResult = await PR.api("/api/drive/sync", { method: "POST", body: {} });
          s.drive = await PR.api("/api/drive/status");
          PR.toast(s.driveResult.message || PR.t("同步完成"));
          return true;
        }
        if (action === "refresh") { await load(s); return true; }
        if (action === "reveal") { await PR.api("/api/library/reveal", { method: "POST", body: {} }); return true; }
        if (action === "restart") {
          if (window.easyreadDesktop && window.easyreadDesktop.relaunch) await window.easyreadDesktop.relaunch();
          else PR.toast(PR.t("请关掉 EasyRead 再重新打开"));
          return true;
        }
        if (action === "forget") { s.cloudCustom = null; redraw(); return true; }
        if (action === "cleanup") {
          if (!(await PR.confirm({ title: PR.t("清理未完成的迁移？"), body: PR.t("只删除上次迁移复制到 {path} 的内容，那里原有的论文和你现在用的文献库都不动。", { path: b.dataset.path }), ok: PR.t("清理") }))) return true;
          busy = true; redraw();
          await PR.api("/api/library/cleanup", { method: "POST", body: { path: b.dataset.path } });
          if (s.cloudCustom && s.cloudCustom.path === b.dataset.path) s.cloudCustom = await PR.api("/api/library/inspect", { method: "POST", body: { path: b.dataset.path } });
          busy = false; await load(s);
          PR.toast(PR.t("已清理"));
          return true;
        }
        if (disabled(s)) return true;
        if (action === "custom") {
          const desktop = window.easyreadDesktop;
          let path = desktop && desktop.pickFolder ? await desktop.pickFolder() : null;
          if (!(desktop && desktop.pickFolder)) {  // 浏览器版：后端弹系统的选文件夹窗口，弹不出来才让人填路径
            const r = await PR.api("/api/library/pick-folder", { method: "POST", body: {} }).catch(() => ({ supported: false }));
            path = r.supported ? r.path : await PR.promptText({
              title: PR.t("文献库文件夹"), body: PR.t("填写网盘同步文件夹或已有 EasyRead 文献库的完整路径。") + "\n" +
                PR.t("普通目录会使用其下的 EasyRead 子文件夹；已有文献库直接使用。"), max: 4096,
            });
          }
          if (!path) return true;
          const picked = await PR.api("/api/library/inspect", { method: "POST", body: { path } });
          if (picked.incomplete || picked.papers || !picked.writable) { s.cloudCustom = picked; return true; }
          return await migrate(s, "copy", picked.path);
        }
        if (!["copy", "use", "merge"].includes(action)) return false;
        return await migrate(s, action, b.dataset.path);
      } catch (err) {
        s.cloudError = err.message;
        PR.toast(err.message);
      } finally { busy = false; redraw(); }
      return true;
    },
  };

  /* 确认 → 迁移（复制并核对，成功后以后都存到新位置）/ 切换 / 合并 */
  async function migrate(s, action, target) {
    try {
        busy = true; redraw();
        const d = await PR.api("/api/library/location");
        s.cloud = d;
        const dest = await PR.api("/api/library/inspect", { method: "POST", body: { path: target } });
        const path = dest.path || target;
        const mine = PR.t("{n} 篇论文 · {size}", { n: d.papers, size: size(d.bytes || 0) });
        const first = action === "use" ? PR.t("以后直接用那里的论文")
          : action === "merge" ? PR.t("重复的论文会跳过")
          : PR.t("复制完逐个核对，以后都存在新位置");
        const yes = await PR.migrateDialog({
          title: action === "use" ? PR.t("切换文献库") : action === "merge" ? PR.t("合并文献库") : PR.t("迁移文献库"),
          from: d.path, to: path,
          meta: action === "use" ? PR.t("那里有 {n} 篇论文", { n: dest.papers }) : mine,
          notes: [first, PR.t("原来的文件夹保留，不会删除"), PR.t("开始前请关闭其他阅读窗口，完成后需要重启")],
          ok: action === "use" ? PR.t("切换") : action === "merge" ? PR.t("开始合并") : PR.t("开始迁移"),
        });
        if (!yes) return true;
        s.cloudResult = await PR.api("/api/library/move", { method: "POST", body: { path, mode: action } });
        if (s.cloudResult.ok === false) throw new Error(s.cloudResult.message);
        s.cloud.restart_required = true; s.cloudCustom = null;
        PR.libraryLocationNotice({ library_status: "restart_required" });
    } finally { busy = false; redraw(); }
    return true;
  }

  PR.libraryLocationNotice = function (data) {
    let node = PR.$("#cloudNotice");
    const restart = data.library_status === "restart_required";
    const host = data.other_device;
    if (!restart && (!host || host === dismissedHost)) { if (node) node.remove(); return; }
    if (!node) {
      node = PR.el("div", { id: "cloudNotice", class: "cloud-notice", role: "status" });
      PR.$(".main").insertBefore(node, PR.$(".list-head"));
    }
    node.innerHTML = '<span>' + PR.esc(restart ? PR.t("文献库位置已更改，请先重启再继续阅读。") :
      PR.t("文献库可能正在另一台电脑（{host}）上打开。同时修改同一篇论文，改动会互相覆盖。", { host })) + "</span>" +
      (!restart ? '<button class="btn sm" data-cloud-dismiss>' + PR.t("关闭") + "</button>" : "");
    node.onclick = (e) => { if (!restart && e.target.closest("[data-cloud-dismiss]")) { dismissedHost = host; node.remove(); } };
  };
})(window.PR);
