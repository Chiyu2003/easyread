import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from easyread import recommendations
from easyread.app import config
from easyread.chat import chat, chat_models


class Workspace:
    def __init__(self, pid, title, opened="", doi="", abstract="An audio language model study."):
        self.id = pid
        self.data = {
            "paper": {"meta": {"title_en": title, "doi": doi, "abstract_en": abstract}, "blocks": []},
            "item": {"last_opened": opened, "tags": []},
        }

    def load(self, name):
        return self.data[name]


class Library:
    def __init__(self, workspaces):
        self.root = Path("/tmp/easyread-recommendations-test")
        self.workspaces = workspaces

    def all(self):
        return self.workspaces


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        recommendations._CACHE.clear()

    def test_recent_seeds_recommendations_exclude_library_items_and_old_results(self):
        lib = Library([
            Workspace("seed-a", "Earlier audio paper", "2026-10-12T10:00:00Z", "10.1000/seed-a"),
            Workspace("seed-b", "Recent audio paper", "2026-10-13T10:00:00Z", "10.1000/seed-b"),
            Workspace("saved", "Already in my library", ""),
            Workspace("never-opened", "Never opened", ""),
        ])

        def fake_get_json(url, timeout=20):
            path = urlparse(url).path
            if "/graph/v1/paper/DOI:" in path:
                return {"paperId": "seed-paper-id"}
            if "/recommendations/v1/papers/forpaper/" in path:
                return {"recommendedPapers": [
                    {"paperId": "related-1", "title": "A new audio-language study", "year": 2025,
                     "venue": "TestConf", "authors": [{"name": "A. Researcher"}],
                     "externalIds": {"DOI": "10.1000/related"}, "abstract": "An abstract about audio-language models."},
                    {"paperId": "already-saved", "title": "Already in my library", "year": 2025,
                     "externalIds": {}, "abstract": "Duplicate."},
                    {"paperId": "too-old", "title": "Old related study", "year": 2020,
                     "externalIds": {}, "abstract": "Old."},
                ]}
            if "/graph/v1/paper/search?" in url:
                return {"data": []}
            raise AssertionError(url)

        with patch.object(recommendations, "_get_json", side_effect=fake_get_json):
            result = recommendations.find(lib, recent=5, years=3)

        self.assertEqual([item["paper_id"] for item in result["items"]], ["related-1"])
        self.assertEqual([seed["title"] for seed in result["seeds"]], ["Recent audio paper", "Earlier audio paper"])
        self.assertEqual(result["items"][0]["doi"], "10.1000/related")
        self.assertEqual(result["items"][0]["url"], "https://doi.org/10.1000/related")

    def test_crossref_only_enriches_a_close_title_match(self):
        paper = {"title": "A precise title for a paper", "doi": "", "venue": ""}

        def fake_get_json(url, timeout=20):
            return {"message": {"items": [{"title": ["A precise title for a paper"], "DOI": "10.5555/PAPER",
                                             "container-title": ["Journal of Tests"],
                                             "published-print": {"date-parts": [[2024, 2, 3]]},
                                             "URL": "https://doi.org/10.5555/PAPER"}]}}

        with patch.object(recommendations, "_get_json", side_effect=fake_get_json):
            enriched = recommendations._crossref(paper)
        self.assertEqual(enriched["doi"], "10.5555/paper")
        self.assertEqual(enriched["venue"], "Journal of Tests")
        self.assertEqual(enriched["year"], "2024")

    def test_summary_uses_configured_chat_model_and_structured_fields(self):
        response = '{"translation":"忠實翻譯。","summary":{"question":"問題","method":"方法","results":"結果","limitations":"摘要未說明。"}}'
        with patch.object(config, "load", return_value={}), \
                patch.object(chat_models, "engine_cfg", return_value=({"engine": "mock"}, {"id": "mock"})), \
                patch.object(chat_models, "label", return_value="Mock Model"), \
                patch.object(chat, "stream", return_value=iter([response])) as stream:
            result = recommendations.summarise("Title", "English abstract")
        self.assertEqual(result["translation"], "忠實翻譯。")
        self.assertEqual(result["summary"]["question"], "問題")
        self.assertEqual(result["model"], "Mock Model")
        stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
