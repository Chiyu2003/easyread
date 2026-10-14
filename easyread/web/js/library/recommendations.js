/* 基于最近阅读论文的延伸阅读推荐。 */
(function (PR) {
  "use strict";
  const dlg = PR.$("#recommendDlg"), box = dlg.querySelector(".dialog");
  let rows = [], loading = false, includeOlder = false, returnFocusEl = null;

  function safeUrl(value) {
    try { const u = new URL(value); return ["https:", "http:"].includes(u.protocol) ? u.href : ""; } catch (_) { return ""; }
  }
  function header() {
    return '<div class="rec-head"><div><h2 id="recommendTitle">' + PR.t("延伸阅读") + '</h2><p class="hint">' +
      PR.t("根据最近阅读的 5 篇论文检索相关研究；优先显示近 3 年结果。") + '</p></div><button class="btn icon" data-close aria-label="' + PR.t("关闭") + '">×</button></div>' +
      '<label class="rec-older"><input type="checkbox" data-older ' + (includeOlder ? "checked" : "") + '> ' + PR.t("也显示更早的论文") + '</label>';
  }
  function render(message) {
    const active = document.activeElement;
    const focused = active && box.contains(active) && active.matches("[data-close], [data-refresh], [data-older], [data-summary]") ? active : null;
    const selector = focused && (focused.hasAttribute("data-summary") ? '[data-summary="' + focused.dataset.summary + '"]' :
      focused.hasAttribute("data-older") ? "[data-older]" : focused.hasAttribute("data-refresh") ? "[data-refresh]" : "[data-close]");
    box.innerHTML = header() + (message ? '<div class="rec-message" role="status">' + PR.esc(message) + "</div>" : "") +
      (loading ? '<div class="rec-loading"><span class="spin"></span> ' + PR.t("正在查找相关论文…") + "</div>" : "") +
      (rows.length ? '<div class="rec-list">' + rows.map((p, n) => {
        const href = safeUrl(p.url) || (p.doi ? safeUrl("https://doi.org/" + p.doi) : "");
        const publisher = [p.venue, p.year].filter(Boolean).join(" · ");
        return '<article class="rec-card" data-rec="' + n + '"><h3>' + PR.esc(p.title) + '</h3>' +
          '<div class="rec-meta">' + PR.esc([p.authors, publisher, p.doi ? "DOI: " + p.doi : ""].filter(Boolean).join(" · ")) + '</div>' +
          (p.related_to && p.related_to.length ? '<div class="rec-related">' + PR.t("相关于：") + PR.esc(p.related_to.join(", ")) + '</div>' : "") +
          '<div class="rec-actions">' + (href ? '<a class="btn sm line" href="' + PR.esc(href) + '" target="_blank" rel="noopener">' + PR.t("查看来源") + '</a>' : "") +
          '<button class="btn sm accent" data-summary="' + n + '" ' + (!p.abstract || p.summaryLoading ? "disabled" : "") + '>' + (p.summaryLoading ? PR.t("正在生成…") : PR.t("翻译摘要并整理")) + '</button></div>' +
          (!p.abstract ? '<p class="hint">' + PR.t("来源没有提供摘要，暂时无法生成摘要内容。") + '</p>' : "") +
          (p.summary ? '<div class="rec-summary"><div class="hint">' + PR.t("使用模型：{name}", { name: PR.esc(p.summary.model) }) + '</div><pre>' + PR.esc(p.summary.text) + '</pre></div>' : "") +
          (p.summaryError ? '<div class="rec-error">' + PR.esc(p.summaryError) + '</div>' : "") + '</article>';
      }).join("") + '</div>' : (!loading && !message ? '<div class="rec-message">' + PR.t("没有找到可显示的推荐论文。") + '</div>' : "")) +
      '<div class="actions"><button class="btn line" data-refresh ' + (loading ? "disabled" : "") + '>' + PR.t("重新搜索") + '</button><button class="btn" data-close>' + PR.t("关闭") + '</button></div>';
    PR.translateDom(box);
    if (selector) {
      const next = box.querySelector(selector);
      if (next && !next.disabled) next.focus({ preventScroll: true });
      else { const closeButton = box.querySelector("[data-close]"); if (closeButton) closeButton.focus({ preventScroll: true }); }
    }
  }
  async function search() {
    loading = true; rows = []; render();
    try {
      const d = await PR.api("/api/recommendations" + (includeOlder ? "?older=1" : ""));
      rows = d.items || []; loading = false;
      render(d.message ? PR.t(d.message) : (d.partial ? PR.t("部分来源暂时无法连接，以下为已取得的结果。") : ""));
    } catch (e) { loading = false; render(PR.t(e.message) || PR.t("推荐搜索失败，请稍后重试。")); }
  }
  function close() {
    dlg.classList.remove("open");
    if (returnFocusEl && returnFocusEl.isConnected) returnFocusEl.focus();
  }
  function open() {
    returnFocusEl = document.activeElement;
    dlg.classList.add("open"); includeOlder = false;
    search();
    requestAnimationFrame(() => { const closeButton = box.querySelector("[data-close]"); if (closeButton) closeButton.focus(); });
  }
  PR.$("#recommendBtn").addEventListener("click", open);
  dlg.addEventListener("click", async (e) => {
    if (e.target === dlg || e.target.closest("[data-close]")) { close(); return; }
    if (e.target.closest("[data-refresh]")) return search();
    const button = e.target.closest("[data-summary]");
    if (!button) return;
    const index = Number(button.dataset.summary), paper = rows[index];
    if (!paper || !paper.abstract) return;
    paper.summaryLoading = true; paper.summaryError = ""; render();
    try { paper.summary = await PR.api("/api/recommendations/summarize", { method: "POST", body: { paper } }); }
    catch (err) { paper.summaryError = PR.t(err.message) || PR.t("摘要生成失败，请重试。"); }
    paper.summaryLoading = false;
    render();
  });
  dlg.addEventListener("change", (e) => {
    if (e.target.matches("[data-older]")) { includeOlder = e.target.checked; search(); }
  });
  document.addEventListener("keydown", (e) => {
    if (!dlg.classList.contains("open")) return;
    if (e.key === "Escape") { close(); return; }
    if (e.key !== "Tab") return;
    const focusable = Array.from(box.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), [tabindex="0"]'))
      .filter((el) => el.offsetParent !== null);
    if (!focusable.length) { e.preventDefault(); box.focus(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === box)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
})(window.PR);
