"""界面语言：系统语言判断、tr 填占位符、页面注入词典，以及所有界面文字都有英文。  python -m unittest tests.test_i18n"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from easyread import i18n

ROOT = Path(__file__).resolve().parents[1]


class I18nTest(unittest.TestCase):
    def setUp(self):
        i18n.system_lang.cache_clear()

    def tearDown(self):
        i18n.system_lang.cache_clear()

    def test_system_lang_from_electron(self):
        for tag, want in (("zh-CN", "zh"), ("zh-TW", "zh-Hant"), ("zh-HK", "zh-Hant"),
                          ("zh-Hant", "zh-Hant"), ("en-US", "en"), ("de", "en"), ("ja", "en")):
            i18n.system_lang.cache_clear()
            with mock.patch.dict(os.environ, {"EASYREAD_SYSTEM_LANG": tag}):
                self.assertEqual(i18n.system_lang(), want, tag)

    def test_system_lang_from_env(self):
        env = {k: v for k, v in os.environ.items() if k not in ("EASYREAD_SYSTEM_LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE")}
        env["LANG"] = "de_DE.UTF-8"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(i18n.system_lang(), "en")

    def test_choice_overrides_system(self):
        with mock.patch.object(i18n, "system_lang", lambda: "zh"), mock.patch.object(i18n, "choice", lambda: "en"):
            self.assertEqual(i18n.lang(), "en")
        with mock.patch.object(i18n, "system_lang", lambda: "en"), mock.patch.object(i18n, "choice", lambda: "auto"):
            self.assertEqual(i18n.lang(), "en")

    def test_tr(self):
        fake = {"已导入 {n} 篇": "Imported {n} papers"}
        with mock.patch.object(i18n, "en_dict", lambda: fake):
            with mock.patch.object(i18n, "lang", lambda: "en"):
                self.assertEqual(i18n.tr("已导入 {n} 篇", n=3), "Imported 3 papers")
                self.assertEqual(i18n.tr("词典里没有"), "词典里没有")
            with mock.patch.object(i18n, "lang", lambda: "zh"):
                self.assertEqual(i18n.tr("已导入 {n} 篇", n=3), "已导入 3 篇")
            with mock.patch.object(i18n, "hant_dict", lambda: {"已导入 {n} 篇": "已匯入 {n} 篇"}):
                with mock.patch.object(i18n, "lang", lambda: "zh-Hant"):
                    self.assertEqual(i18n.tr("已导入 {n} 篇", n=3), "已匯入 3 篇")

    def test_inject(self):
        page = '<html lang="zh-CN">\n<head><title>EasyRead</title></head>'
        self.assertNotIn("pr-i18n", i18n.inject(page, "zh"))
        with mock.patch.object(i18n, "en_dict", lambda: {"设置": "Settings</script>"}):
            out = i18n.inject(page, "en")
        self.assertIn('<html lang="en"', out)
        self.assertIn('id="pr-i18n"', out)
        self.assertNotIn("Settings</script>", out)  # 词典里的 < 要转义，不能提前结束 script
        with mock.patch.object(i18n, "hant_dict", lambda: {"设置": "設定"}):
            out = i18n.inject(page, "zh-Hant")
        self.assertIn('<html lang="zh-TW"', out)
        self.assertIn('id="pr-i18n-hant"', out)

    def test_every_ui_string_has_english(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "i18n_check.py")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout[-3000:])

    def test_every_ui_string_has_traditional_chinese(self):
        from scripts.i18n_check import collect
        keys, _ = collect()
        hant = i18n.hant_dict()
        self.assertEqual(keys - set(hant), set())


if __name__ == "__main__":
    unittest.main()
