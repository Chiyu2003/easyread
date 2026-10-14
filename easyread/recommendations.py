"""延伸阅读：从最近阅读的论文生成 Semantic Scholar 相关论文列表。"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date

from . import chat, chat_models, config, http
from .i18n import tr

UA = "EasyRead/1.0 (local research reader)"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,authors,year,venue,publicationDate,externalIds,abstract,url"


def _json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with http.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read(5_000_000).decode("utf-8"))


def _norm_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _norm_doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", (value or "").strip(), flags=re.I).lower()


def _recent_seeds(lib) -> list[dict]:
    summaries = sorted(lib.list(), key=lambda p: p.get("last_opened") or p.get("added") or "", reverse=True)
    seeds = []
    for summary in summaries:
        ws = lib.ws(summary["id"])
        if not ws:
            continue
        paper = ws.load("paper") or {}
        meta = paper.get("meta") or {}
        abstract = meta.get("abstract_en") or next(
            (b.get("en", "") for b in paper.get("blocks", []) if b.get("role") == "abstract"), "")
        title = meta.get("title_en") or summary.get("title_en") or ""
        keywords = meta.get("keywords") or paper.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [x.strip() for x in re.split(r"[,;，；]", keywords) if x.strip()]  # i18n-ok
        if title and abstract:
            seeds.append({"id": summary["id"], "title": title, "abstract": abstract[:1800],
                          "keywords": ", ".join(str(x) for x in keywords[:12])})
        if len(seeds) == 5:
            break
    return seeds


def _crossref(doi: str) -> dict:
    if not doi:
        return {}
    try:
        item = (_json("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/"), 12).get("message") or {})
        return {"doi": item.get("DOI") or doi,
                "venue": (item.get("container-title") or [""])[0],
                "year": str(((item.get("published-print") or item.get("published-online") or item.get("created") or {}).get("date-parts") or [[""]])[0][0]),
                "url": item.get("URL") or ""}
    except Exception:
        return {}


def find(lib, include_older: bool = False) -> dict:
    seeds = _recent_seeds(lib)
    if not seeds:
        return {"items": [], "seeds": [], "message": tr("先阅读几篇有摘要的论文，再来找延伸阅读。")}

    existing_dois, existing_titles = set(), set()
    for summary in lib.list():
        ws = lib.ws(summary["id"])
        meta = ((ws.load("paper") or {}).get("meta") or {}) if ws else {}
        if meta.get("doi"):
            existing_dois.add(_norm_doi(meta["doi"]))
        existing_titles.add(_norm_title(meta.get("title_en") or summary.get("title_en") or ""))

    candidates: dict[str, dict] = {}
    errors = []
    for seed_index, seed in enumerate(seeds):
        # Search one seed at a time so highly related work on each recent topic can surface.
        if seed_index:
            time.sleep(1)  # respect Semantic Scholar's unauthenticated request rate
        query = (seed["title"] + " " + seed["keywords"] + " " + seed["abstract"][:700]).strip()
        url = S2_SEARCH + "?" + urllib.parse.urlencode({"query": query, "limit": 15, "fields": S2_FIELDS})
        try:
            for rank, paper in enumerate((_json(url).get("data") or [])):
                title = (paper.get("title") or "").strip()
                ext = paper.get("externalIds") or {}
                doi = _norm_doi(ext.get("DOI") or "")
                key = doi or _norm_title(title)
                if not title or key in existing_dois or key in existing_titles:
                    continue
                row = candidates.setdefault(key, {"title": title, "authors": ", ".join(a.get("name", "") for a in paper.get("authors") or []),
                    "year": str(paper.get("year") or ""), "venue": paper.get("venue") or "", "doi": ext.get("DOI") or "",
                    "abstract": paper.get("abstract") or "", "url": paper.get("url") or "", "related_to": [], "rank": rank})
                if seed["title"] not in row["related_to"]:
                    row["related_to"].append(seed["title"])
                row["rank"] = min(row["rank"], rank)
        except Exception as e:  # per-seed errors still allow partial results
            errors.append(str(e)[:180])

    cutoff = date.today().year - 2
    items = list(candidates.values())
    if not include_older:
        items = [p for p in items if not p["year"].isdigit() or int(p["year"]) >= cutoff]
    items.sort(key=lambda p: (len(p["related_to"]), -p["rank"], int(p["year"]) if p["year"].isdigit() else 0), reverse=True)
    # Crossref provides publication metadata when Semantic Scholar is missing it.
    for paper in items[:8]:
        if paper["doi"] and (not paper["venue"] or not paper["year"]):
            paper.update({k: v for k, v in _crossref(paper["doi"]).items() if v and (k == "doi" or not paper.get(k))})
        if paper["doi"]:
            paper["url"] = "https://doi.org/" + paper["doi"]
    return {"items": items[:20], "seeds": [{"id": s["id"], "title": s["title"]} for s in seeds],
            "message": "" if items else (tr("Semantic Scholar 暂时没有找到符合年份条件的结果。") if not errors else tr("目前无法连接 Semantic Scholar，请稍后重试。")),
            "partial": bool(errors and items)}


def summarize(paper: dict, model_id: str | None = None) -> dict:
    abstract = str(paper.get("abstract") or "").strip()
    if not abstract:
        raise ValueError(tr("这篇论文没有可用的摘要。"))
    cfg = config.load()
    ecfg, model = chat_models.engine_cfg(cfg, model_id)
    prompt = ("你是學術論文閱讀助理。請只根據以下英文原文摘要作答，不要補入摘要沒有的事實。\n"  # i18n-ok
        "請輸出繁體中文，格式固定為兩個標題：\n"  # i18n-ok
        "## 忠實翻譯\n逐句忠實翻譯原文摘要，保留數值、限制語氣和專有名詞。\n"  # i18n-ok
        "## 研究摘要\n以四個項目整理：研究問題、方法、主要結果、限制。摘要沒有明說的項目請寫「摘要未說明」，不要推測。\n\n"  # i18n-ok
        f"論文：{str(paper.get('title') or '')[:500]}\n英文摘要：\n{abstract[:12000]}"  # i18n-ok
        )
    text = "".join(chat.stream(ecfg, prompt, config.HOME, threading.Event()))
    if not text.strip():
        raise ValueError(tr("模型没有返回摘要，请重试。"))
    return {"text": text, "model": chat_models.label(model)}
