/* Lab runner profiles and Drive-backed experiment queue. */
(function (PR) {
  "use strict";
  const backdrop = PR.$("#experimentsDlg"), dialog = () => backdrop.querySelector(".dialog");
  let data = { profiles: [], runs: [], drive: { connected: false } }, selected = "";
  const sample = () => ({ id: "baseline", name: "Baseline experiment", ssh_alias: "lab-gpu", docker_image: "research/image:latest",
    command_template: ["python", "train.py", "--seed", "{seed}"], params: { seed: { type: "integer", default: 1 } },
    mounts: [], output_path: "/workspace/output", host_output_path: "/var/tmp/easyread-runs" });
  async function refresh() { const [runs, drive] = await Promise.all([PR.api("/api/experiments"), PR.api("/api/drive/status")]); data = runs; data.drive = drive; }
  function render() {
    const profile = data.profiles.find((p) => p.id === selected) || data.profiles[0];
    selected = profile ? profile.id : selected;
    const runRows = data.runs.length ? data.runs.map((r) => '<li><b>' + PR.esc(r.state) + '</b> · ' + PR.esc(r.id) +
      (r.paper_id ? ' · ' + PR.t("论文 {id}", { id: PR.esc(r.paper_id) }) : "") + (r.exit_code == null ? "" : ' · ' + PR.t("退出码 {code}", { code: PR.esc(r.exit_code) })) +
      (r.output_files && r.output_files.length ? ' · ' + PR.t("输出文件：{names}", { names: PR.esc(r.output_files.map((x) => x.name).join(", ")) }) : "") +
      (r.log ? '<details><summary>' + PR.t("日志") + '</summary><pre>' + PR.esc(r.log) + '</pre></details>' : "") + '</li>').join("") : "<li>" + PR.t("还没有实验记录") + "</li>";
    dialog().innerHTML = '<div class="set-head"><h2>' + PR.t("远程实验") + '</h2><button class="linkish" id="experimentRefresh">' + PR.t("刷新状态") + '</button><button class="linkish" id="experimentClose">' + PR.t("关闭") + '</button></div>' +
      '<div class="set-body"><p class="set-lead">' + PR.t("实验设置档会使用实验室 runner 主机上的 SSH alias 远程运行 Docker。Runner 机密只保留在 runner 主机。") + '</p>' +
      '<label class="field"><span>' + PR.t("设置档") + '</span><select class="input" id="experimentProfile">' +
      data.profiles.map((p) => '<option value="' + PR.esc(p.id) + '"' + (p.id === selected ? " selected" : "") + '>' + PR.esc(p.name) + '</option>').join("") +
      '</select></label><div class="actions"><button class="btn sm" id="experimentNew">' + PR.t("新增设置档") + '</button><button class="btn sm" id="experimentSave">' + PR.t("保存设置档 JSON") + '</button></div>' +
      '<label class="field"><span>' + PR.t("设置档 JSON") + '</span><textarea class="input" id="experimentProfileJson" rows="12" spellcheck="false">' +
      PR.esc(JSON.stringify(profile || sample(), null, 2)) + '</textarea></label>' +
      '<p class="hint">' + PR.t("请设置 SSH alias、Docker image、参数数组、挂载路径和输出路径；部署说明见 Google Drive 与 runner 文档。") + '</p>' +
      '<label class="field"><span>' + PR.t("关联论文 ID（选填）") + '</span><input class="input" id="experimentPaper" placeholder="' + PR.t("论文 ID") + '"></label>' +
      '<label class="field"><span>' + PR.t("运行参数（JSON）") + '</span><textarea class="input" id="experimentParams" rows="3" spellcheck="false">{}</textarea></label>' +
      '<button class="btn primary" id="experimentQueue"' + (data.profiles.length && data.drive.connected && data.drive.last_sync && !data.drive.temporary_library ? "" : " disabled") + '>' + PR.t("在 Google Drive 排入实验") + '</button>' +
      (!data.drive.connected || !data.drive.last_sync ? '<p class="hint">' + PR.t("请先连接 Google Drive 并同步文献库。") + '</p>' : "") +
      (data.drive.temporary_library ? '<p class="hint">' + PR.t("临时文献库不能连接 Google Drive 或同步实验任务。") + '</p>' : "") +
      '<h3>' + PR.t("最近实验") + '</h3><ul class="experiment-runs">' + runRows + '</ul></div>';
  }
  async function open() { try { await refresh(); render(); backdrop.classList.add("open"); } catch (e) { PR.toast(e.message); } }
  PR.$("#experimentsBtn").onclick = open;
  backdrop.addEventListener("click", async (e) => {
    if (e.target === backdrop || e.target.closest("#experimentClose")) { backdrop.classList.remove("open"); return; }
    try {
      if (e.target.closest("#experimentRefresh")) { await refresh(); render(); return; }
      if (e.target.closest("#experimentNew")) { selected = "baseline"; data.profiles = []; render(); return; }
      if (e.target.closest("#experimentSave")) {
        const profile = JSON.parse(PR.$("#experimentProfileJson").value);
        const saved = await PR.api("/api/experiments/profiles", { method: "POST", body: profile });
        await refresh(); selected = saved.profile.id; render(); PR.toast(PR.t("设置档已保存")); return;
      }
      if (e.target.closest("#experimentQueue")) {
        const profile = JSON.parse(PR.$("#experimentProfileJson").value);
        const params = JSON.parse(PR.$("#experimentParams").value || "{}");
        const saved = await PR.api("/api/experiments/profiles", { method: "POST", body: profile });
        const run = await PR.api("/api/experiments/queue", { method: "POST", body: { profile_id: saved.profile.id, paper_id: PR.$("#experimentPaper").value.trim(), params } });
        await refresh(); selected = saved.profile.id; render(); PR.toast(run.duplicate ? PR.t("此实验已排队") : PR.t("实验已排队")); return;
      }
    } catch (err) { PR.toast(err.message); }
  });
  backdrop.addEventListener("change", (e) => {
    if (e.target.id === "experimentProfile") { selected = e.target.value; render(); }
  });
})(window.PR);
