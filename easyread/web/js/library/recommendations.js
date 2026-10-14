/* Discover related papers from the reader's recent library activity. */
(function (PR) {
  "use strict";
  const backdrop = PR.$("#recommendDlg"), dialog = PR.$("#recommendDlg .dialog");
  if (!backdrop || !dialog) return;
  const state = { recent: 5, years: 3, items: [], summaries: new Map(), loading: false, error: "", message: "" };
  const value = (name, fallback = "") => PR.esc(name || fallback);
  const href = (raw) => { try { const url = new URL(raw, location.href); return ["https:", "http:"].includes(url.protocol) ? PR.esc(url.href) : "#"; } catch (e) { return "#"; } };

  function shell() {
    dialog.innerHTML = '<div class="recommend-head"><div><h2 id="recommendTitle">' + PR.t("延伸阅读") + '</h2><p>' +
      PR.t("根据最近读过的论文，寻找相关研究并排除文献库已有的论文。") +
      '</p></div><button class="btn icon" data-rec-close aria-label="' + PR.t("关闭") + '">' + PR.icon("x", "sm") + '</button></div>' +
      '<div class="recommend-controls"><label>' + PR.t("参考最近阅读") + ' <select class="input" id="recRecent"><option value="3">3</option><option value="5">5</option><option value="10">10</option></select> ' + PR.t("篇论文") + '</label>' +
      '<label>' + PR.t("发表于近") + ' <select class="input" id="recYears"><option value="1">1</option><option value="3">3</option><option value="5">5</option><option value="0">' + PR.t("不限") + '</option></select> ' + PR.t("年") + '</label>' +
      '<button class="btn accent" id="recSearch"' + (state.loading ? ' disabled' : '') + '>' + (state.loading ? '<span class="spin"></span> ' + PR.t("搜索中…") : PR.t("搜索延伸论文")) + '</button></div>' +
      '<div class="recommend-status" role="status">' + (state.error ? '<span class="err">' + PR.esc(state.error) + '</span>' : value(state.message)) + '</div>' +
      '<div class="recommend-results">' + (state.items.length ? state.items.map(card).join("") : state.loading ? '<div class="recommend-empty"><span class="spin"></span> ' + PR.t("正在根据最近读过的论文搜索…") + '</div>' : state.error ? '<div class="recommend-empty">' + PR.esc(state.error) + '</div>' : state.message ? '<div class="recommend-empty">' + PR.esc(state.message) + '</div>' : '<div class="recommend-empty">' + PR.t("按“搜索延伸论文”开始。") + '</div>') + '</div>';
    PR.$("#recRecent", dialog).value = String(state.recent);
    PR.$("#recYears", dialog).value = String(state.years);
  }

  function card(item) {
    const existing = state.summaries.get(item.paper_id);
    const meta = [item.venue, item.year].filter(Boolean).map(value).join(" · ");
    const doi = item.doi ? '<a href="https://doi.org/' + encodeURIComponent(item.doi).replace(/%2F/gi, "/") + '" target="_blank" rel="noopener">DOI: ' + value(item.doi) + '</a>' : "";
    const publisher = item.url ? '<a href="' + href(item.url) + '" target="_blank" rel="noopener">' + PR.t("来源页面") + '</a>' : "";
    const links = [doi, publisher].filter(Boolean).join('<span>·</span>');
    const abstract = item.abstract ? '<details class="recommend-abstract"><summary>' + PR.t("查看原文摘要") + '</summary><p lang="en">' + value(item.abstract) + '</p></details>' : '<p class="recommend-missing">' + PR.t("此来源没有提供摘要，暂时无法翻译或整理。") + '</p>';
    const summaryHtml = existing ? '<section class="recommend-ai"><h4>' + PR.t("繁体中文摘要翻译") + '</h4><p>' + value(existing.translation) + '</p><h4>' + PR.t("研究问题、方法、结果与限制") + '</h4><dl>' +
      [["question", PR.t("研究问题")], ["method", PR.t("方法")], ["results", PR.t("主要结果")], ["limitations", PR.t("限制")]].map(([key, label]) => '<dt>' + label + '</dt><dd>' + value(existing.summary[key]) + '</dd>').join("") + '</dl><small>' + PR.t("使用模型：{model}", { model: value(existing.model) }) + '</small></section>' : "";
    const button = existing ? "" : '<button class="btn sm line" data-rec-summary="' + value(item.paper_id) + '"' + (!item.abstract ? ' disabled' : '') + '>' + PR.icon("sparkle", "sm") + PR.t("翻译摘要并整理研究重点") + '</button>';
    const loading = state.summaries.get(item.paper_id + ":loading");
    return '<article class="recommend-card"><div class="recommend-card-head"><h3><a href="' + href(item.url || "#") + '" target="_blank" rel="noopener">' + value(item.title) + '</a></h3><span class="recommend-score">' + PR.t("相关") + '</span></div>' +
      '<p class="recommend-meta">' + value(item.authors) + (meta ? ' · ' + meta : "") + (links ? ' · ' + links : "") + '</p>' +
      (item.related_to && item.related_to.length ? '<p class="recommend-reason">' + PR.t("相关论文：") + item.related_to.map(value).join("、") + '</p>' : "") + abstract + // i18n-ok separator only
      (loading ? '<p class="recommend-loading"><span class="spin"></span> ' + PR.t("正在翻译并整理…") + '</p>' : button) + summaryHtml + '</article>';
  }

  async function search() {
    state.recent = Number(PR.$("#recRecent", dialog).value || 5);
    state.years = Number(PR.$("#recYears", dialog).value || 0);
    state.loading = true; state.error = ""; state.message = ""; shell();
    try {
      const result = await PR.api('/api/recommendations?recent=' + state.recent + '&years=' + state.years);
      state.items = result.items || [];
      state.message = result.unavailable ? PR.t("目前无法连接 Semantic Scholar，请稍后重试。") : (result.message || PR.t("找到 {n} 篇延伸论文。", { n: state.items.length }));
      if (result.unavailable) state.error = state.message;
    } catch (error) { state.items = []; state.error = error.message || PR.t("推荐搜索失败，请稍后重试。"); }
    finally { state.loading = false; shell(); }
  }

  async function summarise(button) {
    const item = state.items.find((candidate) => candidate.paper_id === button.dataset.recSummary);
    if (!item || !item.abstract) return;
    state.summaries.set(item.paper_id + ":loading", true); shell();
    try {
      const result = await PR.api("/api/recommendations/summary", { method: "POST", body: { title: item.title, abstract: item.abstract } });
      state.summaries.delete(item.paper_id + ":loading");
      state.summaries.set(item.paper_id, result);
    } catch (error) {
      state.summaries.delete(item.paper_id + ":loading");
      state.error = error.message || PR.t("AI摘要失败，请稍后重试。");
    }
    shell();
  }

  PR.$("#recommendBtn").addEventListener("click", () => { state.error = ""; state.message = ""; shell(); backdrop.classList.add("open"); PR.$("#recSearch", dialog).focus(); });
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop || event.target.closest("[data-rec-close]")) backdrop.classList.remove("open");
    if (event.target.closest("#recSearch")) search();
    const summary = event.target.closest("[data-rec-summary]");
    if (summary) summarise(summary);
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") backdrop.classList.remove("open"); });
})(window.PR);
