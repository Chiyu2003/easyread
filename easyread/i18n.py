"""界面语言：中文系统显示中文，其余显示英文；设置里可以手动指定。

代码里只写中文：前端 `PR.t("中文 {n}", {n})`，后端 `tr("中文 {n}", n=…)`。
中文原文就是词条的键，英文在 web/i18n/en.json；缺了哪条英文，tests/test_i18n.py 会报出来。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# 不导入 config：config 间接导入了很多用 tr 的模块，这里保持没有包内依赖
EN_PATH = Path(__file__).resolve().parent / "web" / "i18n" / "en.json"
HANT_PATH = Path(__file__).resolve().parent / "web" / "i18n" / "zh-Hant.json"
CHOICES = ("auto", "zh", "zh-Hant", "en")


@lru_cache(maxsize=1)
def _dict_cached(mtime: float) -> dict:
    return json.loads(EN_PATH.read_text(encoding="utf-8"))


def en_dict() -> dict:
    try:
        return _dict_cached(EN_PATH.stat().st_mtime)
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _hant_cached(mtime: float) -> dict:
    return json.loads(HANT_PATH.read_text(encoding="utf-8"))


def hant_dict() -> dict:
    try:
        return _hant_cached(HANT_PATH.stat().st_mtime)
    except (OSError, ValueError):
        return {}


def _is_zh(tag: str | None) -> bool:
    tag = (tag or "").strip().lower().replace("_", "-")
    return tag.startswith("zh") or tag.startswith("chinese")


def _language(tag: str | None) -> str:
    normalized = (tag or "").strip().lower().replace("_", "-")
    if not _is_zh(normalized):
        return "en"
    if "hant" in normalized or any(part in ("tw", "hk", "mo") for part in normalized.split("-")):
        return "zh-Hant"
    return "zh"


@lru_cache(maxsize=1)
def system_lang() -> str:
    # 桌面版由 Electron 传进来（macOS 从启动台打开时拿不到 LANG）
    tag = os.environ.get("EASYREAD_SYSTEM_LANG")
    if tag:
        return _language(tag)
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var)
        if value and value not in ("C", "POSIX", "C.UTF-8"):
            return _language(value)
    if sys.platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "zh-Hant" if lang_id in (0x0404, 0x0C04, 0x1404) else "zh" if lang_id & 0x3FF == 0x04 else "en"
        except (AttributeError, OSError):
            pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=3).stdout
            first = next((line.strip(' ",()') for line in out.splitlines() if line.strip(' ",()')), "")
            return _language(first)
        except (OSError, subprocess.SubprocessError):
            pass
    return "zh"  # 判断不出来时保持原来的中文


def choice() -> str:
    forced = os.environ.get("EASYREAD_LANG")  # 测试、排查问题时临时指定，优先于设置
    if forced in ("zh", "zh-Hant", "en"):
        return forced
    from . import prefs
    value = (prefs.load().get("ui") or {}).get("lang")
    return value if value in CHOICES else "auto"


def lang() -> str:
    value = choice()
    return value if value in ("zh", "zh-Hant", "en") else system_lang()


def tr(text: str, **kw) -> str:
    """中文原文 → 当前语言；{name} 占位符用 kw 填。"""
    language = lang()
    if language == "en":
        text = en_dict().get(text) or text
    elif language == "zh-Hant":
        text = hant_dict().get(text) or text
    return text.format(**kw) if kw else text


def inject(page: str, language: str | None = None) -> str:
    """返回页面时写上语言，英文时把词典也塞进去，前端同步就能用。"""
    picked = choice() if language is None else language  # 设置里的“界面语言”要显示当前选的是哪项
    language = language or lang()
    from . import config, langs  # 用到时再导入，见文件开头
    target = langs.valid(config.load().get("target"))  # 设置里的译文语言：按钮文字、没译过的论文用它
    html_lang = "en" if language == "en" else "zh-TW" if language == "zh-Hant" else "zh-CN"
    page = page.replace('<html lang="zh-CN">', f'<html lang="{html_lang}" data-lang-choice="{picked}" data-target="{target}">', 1)
    if language == "en":
        payload = json.dumps(en_dict(), ensure_ascii=False).replace("<", "\\u003c")
        page = page.replace("</title>", f'</title>\n<script id="pr-i18n" type="application/json">{payload}</script>', 1)
    elif language == "zh-Hant":
        payload = json.dumps(hant_dict(), ensure_ascii=False).replace("<", "\\u003c")
        page = page.replace("</title>", f'</title>\n<script id="pr-i18n-hant" type="application/json">{payload}</script>', 1)
    return page
