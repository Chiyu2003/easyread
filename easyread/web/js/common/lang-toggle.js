/* 顶栏的中文 / EN 切换按钮：中文界面显示 EN，英文界面返回上次使用的中文。
   “跟随系统”在设置 → 阅读 → 界面语言里选。离线单文件版没有服务存设置，不显示。 */
(function (PR) {
  "use strict";
  const btn = PR.$("#langBtn");
  if (!btn) return;
  if (location.protocol === "file:") { btn.hidden = true; return; }
  const lastChinese = () => PR.ls.get("easyread-last-chinese-lang", "zh");
  if (PR.lang !== "en") PR.ls.set("easyread-last-chinese-lang", PR.langChoice === "auto" ? PR.lang : PR.langChoice);
  const targetChinese = lastChinese();
  btn.textContent = PR.lang === "en" ? (targetChinese === "zh-Hant" ? "繁" : "中") : "EN";  // i18n-ok 按钮上写的是要切换到的语言
  if (PR.lang === "en") btn.title = targetChinese === "zh-Hant" ? PR.t("切换到繁体中文界面") : PR.t("切换到简体中文界面");
  else btn.title = PR.t("切换到英文界面");
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const lang = PR.lang === "en" ? lastChinese() : "en";
      if (lang === "zh" || lang === "zh-Hant") PR.ls.set("easyread-last-chinese-lang", lang);
      await PR.api("/api/prefs", { method: "POST", body: { ui: { lang } } });
      try { if (PR.lib && PR.lib.lastData) sessionStorage.setItem("easyread-lib-snap", JSON.stringify({ t: Date.now(), d: PR.lib.lastData })); } catch (e) { /* 存不下就普通刷新 */ }
      location.replace(location.href.split("#")[0]);  // 不用 reload：普通跳转才有淡入淡出（library.css 的 @view-transition）
    } catch (e) {
      btn.disabled = false;
      PR.toast(PR.t("保存失败：{msg}", { msg: PR.esc(e.message) }));
    }
  };
})(window.PR);
