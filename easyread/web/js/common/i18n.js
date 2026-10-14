/* 界面语言。代码里只写中文：PR.t("已导入 {n} 篇", { n })；中文原文就是键，英文在 web/i18n/en.json。
   后端返回页面时按系统语言（或设置里选的）写好 <html lang>，英文时把词典塞进 #pr-i18n。 */
window.PR = window.PR || {};
(function (PR) {
  "use strict";

  let en = {}, hant = {};
  try { en = JSON.parse((document.getElementById("pr-i18n") || {}).textContent || "{}"); } catch (e) { en = {}; }
  try { hant = JSON.parse((document.getElementById("pr-i18n-hant") || {}).textContent || "{}"); } catch (e) { hant = {}; }
  const htmlLang = document.documentElement.lang.toLowerCase();
  PR.lang = htmlLang === "en" ? "en" : (htmlLang === "zh-tw" || htmlLang === "zh-hant") ? "zh-Hant" : "zh";
  PR.langChoice = document.documentElement.dataset.langChoice || "auto";  // 设置里选的：auto 跟随系统
  PR.target = document.documentElement.dataset.target || "zh";  // 译文语言（论文翻成什么），名字表在 util.js

  const fill = (s, vars) => vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m)) : s;
  PR.t = (zh, vars) => fill(PR.lang === "en" ? en[zh] || zh : PR.lang === "zh-Hant" ? hant[zh] || zh : zh, vars);

  /* 写死在 HTML 里的中文：按原文整句对照替换，HTML 不用改 */
  const CJK = /[一-鿿]/;
  const ATTRS = ["placeholder", "title", "aria-label", "alt"];
  PR.translateDom = function (root) {
    if (PR.lang === "zh") return;
    const dict = PR.lang === "en" ? en : hant;
    root = root || document.body;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const raw = n.nodeValue, key = raw.trim();
      if (key && CJK.test(key) && dict[key]) n.nodeValue = raw.replace(key, dict[key]);
    }
    root.querySelectorAll("*").forEach((el) => ATTRS.forEach((a) => {
      const v = el.getAttribute(a);
      if (v && CJK.test(v) && dict[v.trim()]) el.setAttribute(a, dict[v.trim()]);
    }));
  };
  if (PR.lang !== "zh") {
    const dict = PR.lang === "en" ? en : hant;
    if (dict[document.title]) document.title = dict[document.title];
    if (document.body) PR.translateDom(document.body);
    else document.addEventListener("DOMContentLoaded", () => PR.translateDom(document.body));
  }
})(window.PR);
