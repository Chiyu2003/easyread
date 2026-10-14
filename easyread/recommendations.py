"""Related-paper discovery from the papers a reader has recently opened."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

from .app.i18n import tr

S2_GRAPH = "https://api.semanticscholar.org/graph/v1/paper/"
S2_RECOMMENDATIONS = "https://api.semanticscholar.org/recommendations/v1/papers/forpaper/"
CROSSREF = "https://api.crossref.org/works"
FIELDS = "title,abstract,year,publicationDate,venue,authors,externalIds,url"
UA = "EasyRead/1.4 (https://github.com/Chiyu2003/easyread)"
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_SECONDS = 15 * 60
_STOP = set("about after again also and are because been before being between both could each from have into more most other over same some such than that the their them then there these they this those through under using very was were what when which while with within without your their based study paper results methods approach model models using task tasks audio language".split())


class RecommendationError(ValueError):
    pass


def _get_json(url: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(5_000_001))


def _norm_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _doi(value: str) -> str:
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", (value or "").strip(), flags=re.I)
    return value.strip().rstrip(".,;)").lower()


def _arxiv(value: str) -> str:
    return re.sub(r"^arxiv:\s*", "", (value or "").strip(), flags=re.I).split("v", 1)[0].lower()


def _abstract(ws, meta: dict) -> str:
    paper = ws.load("paper") or {}
    for block in paper.get("blocks") or []:
        if block.get("role") == "abstract":
            return str(block.get("en") or block.get("caption_en") or block.get("zh") or "").strip()
    return str(meta.get("abstract_en") or "").strip()


def _keywords(meta: dict, abstract: str) -> str:
    keywords = meta.get("keywords") or meta.get("keyword") or []
    if isinstance(keywords, str):
        keywords = re.split(r"[,;|]", keywords)
    explicit = [str(word).strip() for word in keywords if str(word).strip()]
    if explicit:
        return " ".join(explicit[:8])
    words = re.findall(r"[A-Za-z][A-Za-z0-]{3,}", abstract.lower())
    counts: dict[str, int] = {}
    for word in words:
        word = word.strip("-").lower()
        if word not in _STOP:
            counts[word] = counts.get(word, 0) + 1
    return " ".join(word for word, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:6])


def _source_key(meta: dict) -> str:
    doi = _doi(str(meta.get("doi") or ""))
    if doi:
        return "DOI:" + doi
    arxiv = _arxiv(str(meta.get("arxiv") or ""))
    if arxiv:
        return "ARXIV:" + arxiv
    return ""


def _seeds(lib, limit: int) -> list[dict]:
    seeds = []
    for ws in lib.all():
        paper = ws.load("paper") or {}
        meta = dict(paper.get("meta") or {})
        item = ws.load("item") or {}
        meta.update({k: v for k, v in (item.get("meta_override") or {}).items() if v})
        opened = str(item.get("last_opened") or "")
        if not opened:
            continue
        title = str(meta.get("title_en") or meta.get("title_zh") or "").strip()
        abstract = _abstract(ws, meta)
        if not title or not abstract:
            continue
        seeds.append({"id": ws.id, "title": title, "abstract": abstract[:4000], "keywords": _keywords(meta, abstract),
                      "opened": opened, "key": _source_key(meta)})
    seeds.sort(key=lambda seed: seed["opened"], reverse=True)
    return seeds[:limit]


def _library_keys(lib) -> tuple[set[str], set[str], set[str]]:
    dois, arxiv_ids, titles = set(), set(), set()
    for ws in lib.all():
        meta = dict((ws.load("paper") or {}).get("meta") or {})
        item = ws.load("item") or {}
        meta.update({k: v for k, v in (item.get("meta_override") or {}).items() if v})
        if value := _doi(str(meta.get("doi") or "")):
            dois.add(value)
        if value := _arxiv(str(meta.get("arxiv") or "")):
            arxiv_ids.add(value)
        for key in ("title_en", "title_zh"):
            if value := _norm_title(str(meta.get(key) or "")):
                titles.add(value)
    return dois, arxiv_ids, titles


def _s2_fields(paper_id: str) -> dict:
    url = S2_GRAPH + urllib.parse.quote(paper_id, safe=":") + "?fields=paperId,title"
    return _get_json(url)


def _resolve_seed(seed: dict) -> str:
    if seed["key"]:
        try:
            return str(_s2_fields(seed["key"]).get("paperId") or "")
        except Exception:
            pass
    query = urllib.parse.urlencode({"query": seed["title"], "fields": "paperId,title"})
    result = _get_json(S2_GRAPH + "search/match?" + query)
    data = result.get("data") or []
    return str((data[0] if isinstance(data, list) and data else result).get("paperId") or "")


def _search(seed: dict, years: int) -> list[dict]:
    query = " ".join(x for x in (seed["title"], seed["keywords"]) if x).strip()[:240]
    params = {"query": query, "limit": 20, "fields": FIELDS}
    if years:
        params["year"] = f"{time.gmtime().tm_year - years}-{time.gmtime().tm_year}"
    result = _get_json(S2_GRAPH + "search?" + urllib.parse.urlencode(params))
    return list(result.get("data") or [])


def _crossref(paper: dict) -> dict:
    title = str(paper.get("title") or "").strip()
    if not title:
        return {}
    params = {"query.title": title, "rows": 3, "select": "DOI,title,container-title,published-print,published-online,URL"}
    result = _get_json(CROSSREF + "?" + urllib.parse.urlencode(params), timeout=10)
    wanted = _norm_title(title)
    best, score = {}, 0.0
    for entry in (result.get("message") or {}).get("items") or []:
        candidate = _norm_title((entry.get("title") or [""])[0])
        similarity = SequenceMatcher(None, wanted, candidate).ratio()
        if similarity > score:
            best, score = entry, similarity
    if score < 0.88:
        return {}
    issued = (best.get("published-print") or best.get("published-online") or {}).get("date-parts") or []
    year = str(issued[0][0]) if issued and issued[0] else ""
    return {"doi": _doi(best.get("DOI", "")), "venue": (best.get("container-title") or [""])[0],
            "year": year, "publisher_url": best.get("URL", "")}


def _external_ids(paper: dict) -> dict:
    ext = paper.get("externalIds") or {}
    return {"doi": _doi(str(ext.get("DOI") or "")), "arxiv": _arxiv(str(ext.get("ArXiv") or ""))}


def _normalise_paper(paper: dict) -> dict:
    ids = _external_ids(paper)
    doi = ids["doi"]
    arxiv = ids["arxiv"]
    url = ("https://doi.org/" + doi) if doi else ("https://arxiv.org/abs/" + arxiv if arxiv else str(paper.get("url") or ""))
    return {"paper_id": str(paper.get("paperId") or ""), "title": str(paper.get("title") or ""),
            "authors": ", ".join(str(author.get("name") or "") for author in paper.get("authors") or []),
            "year": str(paper.get("year") or ""), "venue": str(paper.get("venue") or ""), "doi": doi,
            "arxiv": arxiv, "url": url, "abstract": str(paper.get("abstract") or ""), "crossref_url": ""}


def find(lib, recent: int = 5, years: int = 3) -> dict:
    recent = min(10, max(1, int(recent)))
    years = min(10, max(0, int(years)))
    seeds = _seeds(lib, recent)
    if not seeds:
        return {"items": [], "seeds": [], "message": tr("先阅读几篇有摘要的论文，再来找延伸阅读。")}
    cache_key = (str(lib.root), tuple((s["id"], s["opened"]) for s in seeds), years)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
            return cached[1]

    dois, arxiv_ids, titles = _library_keys(lib)
    candidates: dict[str, dict] = {}
    failures = 0
    for seed_index, seed in enumerate(seeds):
        succeeded = False
        try:
            sid = _resolve_seed(seed)
            if sid:
                params = {"from": "recent", "limit": 40, "fields": FIELDS}
                related = _get_json(S2_RECOMMENDATIONS + urllib.parse.quote(sid, safe="") + "?" + urllib.parse.urlencode(params))
                succeeded = True
                for rank, paper in enumerate(related.get("recommendedPapers") or []):
                    key = str(paper.get("paperId") or "")
                    if key:
                        entry = candidates.setdefault(key, {"paper": paper, "score": 0.0, "sources": []})
                        entry["score"] += (len(seeds) - seed_index) / (rank + 2)
                        entry["sources"].append(seed["title"])
        except Exception:
            pass
        try:
            for rank, paper in enumerate(_search(seed, years)):
                succeeded = True
                key = str(paper.get("paperId") or "")
                if key:
                    entry = candidates.setdefault(key, {"paper": paper, "score": 0.0, "sources": []})
                    entry["score"] += (len(seeds) - seed_index) * 0.6 / (rank + 2)
                    entry["sources"].append(seed["title"])
        except Exception:
            pass
        if not succeeded:
            failures += 1

    out = []
    year_floor = time.gmtime().tm_year - years if years else 0
    for key, entry in candidates.items():
        item = _normalise_paper(entry["paper"])
        if not item["title"]:
            continue
        if years and item["year"].isdigit() and int(item["year"]) < year_floor:
            continue
        if item["doi"] in dois or item["arxiv"] in arxiv_ids or _norm_title(item["title"]) in titles:
            continue
        item["score"] = entry["score"]
        item["related_to"] = list(dict.fromkeys(entry["sources"]))[:3]
        out.append(item)
    out.sort(key=lambda item: (-item["score"], -(int(item["year"]) if item["year"].isdigit() else 0), item["title"].casefold()))

    # Crossref fills DOI and publication venue where Semantic Scholar lacks them.
    for item in [x for x in out[:10] if not x["doi"] or not x["venue"]]:
        try:
            extra = _crossref(item)
            if extra:
                item["doi"] = item["doi"] or extra.get("doi", "")
                item["venue"] = item["venue"] or extra.get("venue", "")
                item["year"] = item["year"] or extra.get("year", "")
                if extra.get("publisher_url"):
                    item["crossref_url"] = extra["publisher_url"]
                if item["doi"]:
                    item["url"] = "https://doi.org/" + item["doi"]
        except Exception:
            continue

    result = {"items": out[:40], "seeds": [{"id": s["id"], "title": s["title"]} for s in seeds],
              "message": tr("Semantic Scholar暂时没有找到符合年份条件的结果。") if not out and failures < len(seeds) else "",
              "unavailable": not out and failures >= len(seeds)}
    if not result["unavailable"]:
        with _CACHE_LOCK:
            _CACHE[cache_key] = (time.monotonic(), result)
    return result


def summarise(title: str, abstract: str, model_id: str | None = None) -> dict:
    """Use EasyRead's configured chat model to translate and structure an abstract."""
    title, abstract = str(title or "").strip()[:500], str(abstract or "").strip()[:8000]
    if not abstract:
        raise RecommendationError(tr("这篇论文目前没有可用的摘要。"))
    from .app import config
    from .chat import chat_models, chat
    from .engines import engines

    cfg, model = chat_models.engine_cfg(config.load(), model_id)
    prompt = (
        "你是協助研究者閱讀論文的學術助理。只根據提供的英文摘要作答，不得補造摘要未提及的事實。"  # i18n-ok
        "先忠實翻譯摘要成繁體中文，保留限定語、數字、比較對象和不確定性；再用繁體中文分別整理研究問題、方法、主要結果、限制。"  # i18n-ok
        "摘要沒有交代的欄位請填『摘要未說明』。只輸出 JSON，不要 Markdown 圍欄。格式："  # i18n-ok
        '{"translation":"完整繁中翻譯","summary":{"question":"...","method":"...","results":"...","limitations":"..."}}\n'  # i18n-ok
        f"\n論文標題：{title}\n英文原始摘要：\n{abstract}"  # i18n-ok
    )
    pieces = []
    try:
        for piece in chat.stream(cfg, prompt, config.HOME, threading.Event()):
            pieces.append(piece)
    except engines.EngineError as exc:
        raise RecommendationError(str(exc)) from exc
    from .engines.model_json import extract
    parsed, _ = extract("".join(pieces))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("translation"), str) or not isinstance(parsed.get("summary"), dict):
        raise RecommendationError(tr("AI摘要格式无法解析，请再试一次。"))
    fields = {key: str(parsed["summary"].get(key) or tr("摘要未说明。")) for key in ("question", "method", "results", "limitations")}
    return {"translation": parsed["translation"], "summary": fields, "model": chat_models.label(model)}
