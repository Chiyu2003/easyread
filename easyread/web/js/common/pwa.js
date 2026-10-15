/* 可安装外壳与明确选择的单篇离线缓存。离线时写入操作不进入待存队列。 */
(function (PR) {
  "use strict";
  const app = (PR.pwa = { readOnly: !navigator.onLine, installed: false });
  let deferredInstall = null;
  const installButton = document.querySelector("[data-pwa-install]");
  const cacheButton = document.querySelector("[data-pwa-cache]");
  const shelfKey = "easyread-pwa-offline-papers";
  if (installButton) installButton.textContent = PR.t("安裝 EasyRead");
  if (cacheButton) cacheButton.textContent = PR.t("離線閱讀");
  const say = (message) => {
    if (PR.toast) PR.toast(message, null, 5000);
    else {
      let status = document.querySelector("[data-pwa-status]");
      if (!status) {
        status = document.createElement("span");
        status.dataset.pwaStatus = "";
        status.setAttribute("role", "status");
        (document.querySelector(".topbar") || document.body).append(status);
      }
      status.textContent = message;
    }
  };
  const setConnectivity = (offline) => {
    app.readOnly = offline;
    document.documentElement.classList.toggle("pwa-offline", offline);
    if (cacheButton) {
      cacheButton.disabled = offline;
      cacheButton.title = offline ? PR.t("連線後才能更新離線內容") : PR.t("快取目前論文，供離線閱讀");
    }
  };
  app.setConnectivity = setConnectivity;
  window.addEventListener("online", () => setConnectivity(false));
  window.addEventListener("offline", () => setConnectivity(true));
  if (PR.on) PR.on("status", ({ s }) => setConnectivity(s === "offline" || s === "error"));

  async function renderOfflineShelf() {
    const main = document.querySelector(".main");
    if (!main || main.querySelector(".pwa-offline-shelf")) return;
    let papers = [];
    try { papers = JSON.parse(localStorage.getItem(shelfKey) || "[]"); } catch (e) { /* ignore malformed local cache index */ }
    try {
      const names = (await caches.keys()).filter((name) => name.startsWith("easyread-pwa-")).sort();
      if (names.length) {
        const response = await (await caches.open(names[names.length - 1])).match("/__easyread_pwa_offline_index__");
        if (response) papers = await response.json();
      }
    } catch (e) { /* local index is an optional fallback */ }
    if (!papers.length) return;
    const section = document.createElement("section");
    section.className = "pwa-offline-shelf";
    const heading = document.createElement("h2");
    heading.textContent = PR.t("離線論文");
    section.append(heading);
    const list = document.createElement("ul");
    for (const paper of papers) {
      const item = document.createElement("li"), link = document.createElement("a");
      link.href = paper.url;
      link.textContent = paper.title;
      item.append(link); list.append(item);
    }
    section.append(list);
    const listHead = main.querySelector(".list-head");
    if (listHead) listHead.after(section); else main.prepend(section);
  }
  renderOfflineShelf();

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {
      say(PR.t("離線閱讀功能無法啟用，請重新整理頁面"));
    }));
  }
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstall = event;
    if (installButton) installButton.hidden = false;
  });
  window.addEventListener("appinstalled", () => {
    app.installed = true;
    if (installButton) installButton.hidden = true;
    say(PR.t("EasyRead 已安裝"));
  });
  if (installButton) installButton.addEventListener("click", async () => {
    if (!deferredInstall) {
      say(PR.t("請使用瀏覽器選單中的「安裝 EasyRead」"));
      return;
    }
    deferredInstall.prompt();
    await deferredInstall.userChoice;
    deferredInstall = null;
    installButton.hidden = true;
  });

  if (cacheButton) cacheButton.addEventListener("click", async () => {
    if (!navigator.onLine || app.readOnly) return say(PR.t("目前離線，請連線後再準備離線內容"));
    if (!PR.state || !PR.pid || !PR.state.paper) return say(PR.t("請先開啟一篇論文"));
    const urls = new Set([
      location.pathname + location.search,
      "/api/p/" + PR.pid + "/state",
      ...["paper", "discussion", "reader", "layout", "job"].map((name) => "/api/p/" + PR.pid + "/part/" + name),
      ...Array.from(document.querySelectorAll('link[rel="stylesheet"],script[src],link[rel="icon"]'), (el) => el.href || el.src),
    ]);
    for (const page of (PR.state.paper.meta || {}).pages || []) if (page.img) urls.add(PR.imageUrl(page.img));
    urls.add(PR.pdfUrl(1).replace(/#.*$/, ""));
    urls.add("/");
    try {
      const library = await fetch("/", { cache: "no-store" });
      if (library.ok) {
        const doc = new DOMParser().parseFromString(await library.text(), "text/html");
        doc.querySelectorAll('link[rel="stylesheet"],script[src],link[rel="icon"]').forEach((el) => {
          const path = el.href || el.src;
          const asset = path && new URL(path, location.origin);
          if (asset && asset.origin === location.origin) urls.add(asset.href);
        });
      }
    } catch (e) { /* a failed shell fetch will be reported by the worker */ }
    const registration = await navigator.serviceWorker.ready;
    const worker = navigator.serviceWorker.controller || registration.active;
    if (!worker) return say(PR.t("離線功能正在啟動，請稍候再試"));
    cacheButton.disabled = true;
    cacheButton.textContent = PR.t("準備中…");
    const onMessage = async (event) => {
      const data = event.data || {};
      if (data.type !== "CACHE_PROGRESS" && data.type !== "CACHE_DONE") return;
      if (data.type === "CACHE_PROGRESS") cacheButton.textContent = PR.t("快取中 {done}/{total}", { done: data.done, total: data.total });
      else {
        navigator.serviceWorker.removeEventListener("message", onMessage);
        cacheButton.disabled = false;
        cacheButton.textContent = PR.t("離線閱讀");
        if (!data.failed.length) {
          let papers = [];
          try { papers = JSON.parse(localStorage.getItem(shelfKey) || "[]"); } catch (e) { papers = []; }
          const title = PR.state.paper.meta.short_zh || PR.state.paper.meta.title_zh || PR.state.paper.meta.title_en || PR.t("未命名論文");
          const entry = { url: location.pathname + location.search, title };
          papers = [entry, ...papers.filter((paper) => paper.url !== entry.url)].slice(0, 100);
          const serialized = JSON.stringify(papers);
          try { localStorage.setItem(shelfKey, serialized); } catch (e) { /* Cache Storage remains the durable index */ }
          try {
            const names = (await caches.keys()).filter((name) => name.startsWith("easyread-pwa-")).sort();
            if (names.length) await (await caches.open(names[names.length - 1])).put("/__easyread_pwa_offline_index__", new Response(serialized, { headers: { "Content-Type": "application/json" } }));
          } catch (e) { /* paper is still cached even if the shelf index cannot be stored */ }
        }
        say(data.failed.length ? PR.t("已快取 {ok} 項，{failed} 項下載失敗", { ok: data.ok, failed: data.failed.length }) : PR.t("論文已準備好離線閱讀，共 {n} 項", { n: data.ok }));
      }
    };
    navigator.serviceWorker.addEventListener("message", onMessage);
    worker.postMessage({ type: "CACHE_URLS", urls: Array.from(urls) });
  });
})(window.PR || (window.PR = {}));
