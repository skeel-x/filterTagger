import unittest

import filterTagger as ft


def hier(modifier, items, excluded=(), depth=0):
    return {"modifier": modifier,
            "value": {"depth": depth,
                      "excluded": [{"id": i, "label": i} for i in excluded],
                      "items": [{"id": i, "label": i} for i in items]}}


class SceneFilter(unittest.TestCase):
    """The saved filter's object_filter is in the UI's own shape; Stash's
    findScenes wants SceneFilterType. These are real shapes from a v0.31
    Stash."""

    def test_tags_hierarchical(self):
        got = ft.scene_filter({"tags": hier("INCLUDES", ["96", "1476"], ["4"], -1)})
        self.assertEqual(got, {"tags": {"modifier": "INCLUDES", "value": ["96", "1476"],
                                        "excludes": ["4"], "depth": -1}})

    def test_performers_multi(self):
        got = ft.scene_filter({"performers": {"modifier": "INCLUDES_ALL",
                                              "value": {"excluded": [], "items": [{"id": "633", "label": "x"}]}}})
        self.assertEqual(got, {"performers": {"modifier": "INCLUDES_ALL", "value": ["633"], "excludes": []}})

    def test_studios_and_performer_tags_are_hierarchical(self):
        got = ft.scene_filter({"studios": hier("INCLUDES", ["12"]),
                               "performer_tags": hier("EXCLUDES", ["3"], depth=1)})
        self.assertEqual(got["studios"]["depth"], 0)
        self.assertEqual(got["performer_tags"], {"modifier": "EXCLUDES", "value": ["3"], "excludes": [], "depth": 1})

    def test_booleans(self):
        got = ft.scene_filter({"interactive": {"modifier": "EQUALS", "value": "false"},
                               "organized": {"modifier": "EQUALS", "value": "true"},
                               "performer_favorite": {"modifier": "EQUALS", "value": "true"}})
        self.assertEqual(got, {"interactive": False, "organized": True, "performer_favorite": True})

    def test_ints_and_between(self):
        got = ft.scene_filter({"rating100": {"modifier": "GREATER_THAN", "value": {"value": 99}},
                               "performer_age": {"modifier": "BETWEEN", "value": {"value": 18, "value2": 19}},
                               "play_count": {"modifier": "IS_NULL", "value": {}}})
        self.assertEqual(got["rating100"], {"modifier": "GREATER_THAN", "value": 99})
        self.assertEqual(got["performer_age"], {"modifier": "BETWEEN", "value": 18, "value2": 19})
        self.assertEqual(got["play_count"], {"modifier": "IS_NULL"})

    def test_strings(self):
        got = ft.scene_filter({"path": {"modifier": "INCLUDES", "value": "/mnt/adult/"},
                               "url": {"modifier": "MATCHES_REGEX", "value": "eroscripts"},
                               "is_missing": {"modifier": "EQUALS", "value": "performers"}})
        self.assertEqual(got, {"path": {"modifier": "INCLUDES", "value": "/mnt/adult/"},
                               "url": {"modifier": "MATCHES_REGEX", "value": "eroscripts"},
                               "is_missing": "performers"})

    def test_dates_timestamps_resolution_stash_id(self):
        got = ft.scene_filter({
            "date": {"modifier": "BETWEEN", "value": {"value": "2024-01-01", "value2": "2024-12-31"}},
            "created_at": {"modifier": "GREATER_THAN", "value": {"value": "2025-01-01 00:00"}},
            "resolution": {"modifier": "GREATER_THAN", "value": "1080p"},
            "stash_id_endpoint": {"modifier": "IS_NULL", "value": {"endpoint": "", "stashID": ""}},
            "orientation": {"value": ["landscape", "Portrait"]},
            "captions": {"modifier": "INCLUDES", "value": "English"},
        })
        self.assertEqual(got["date"], {"modifier": "BETWEEN", "value": "2024-01-01", "value2": "2024-12-31"})
        self.assertEqual(got["created_at"], {"modifier": "GREATER_THAN", "value": "2025-01-01 00:00"})
        self.assertEqual(got["resolution"], {"modifier": "GREATER_THAN", "value": "FULL_HD"})
        self.assertEqual(got["stash_id_endpoint"], {"modifier": "IS_NULL", "endpoint": "", "stash_id": ""})
        self.assertEqual(got["orientation"], {"value": ["LANDSCAPE", "PORTRAIT"]})
        self.assertEqual(got["captions"], {"modifier": "INCLUDES", "value": "en"})

    def test_empty_filter_matches_everything(self):
        self.assertEqual(ft.scene_filter({}), {})
        self.assertEqual(ft.scene_filter(None), {})

    def test_unknown_criterion_is_refused_not_ignored(self):
        # dropping a criterion would widen the filter and tag too much
        with self.assertRaises(ft.FilterError) as e:
            ft.scene_filter({"made_up": {"modifier": "EQUALS", "value": "x"}})
        self.assertIn("made_up", str(e.exception))

    def test_malformed_criterion_is_refused(self):
        with self.assertRaises(ft.FilterError):
            ft.scene_filter({"tags": "not an object"})
        with self.assertRaises(ft.FilterError):
            ft.scene_filter({"rating100": {"value": {"value": 1}}})  # no modifier

    def test_find_filter_keeps_only_the_search_text(self):
        self.assertEqual(ft.find_filter({"q": "anna", "sort": "date", "direction": "DESC", "per_page": 40}),
                         {"q": "anna"})
        self.assertEqual(ft.find_filter(None), {})
        self.assertEqual(ft.find_filter({"q": ""}), {})


class Funscript(unittest.TestCase):
    def test_path_beside_the_video(self):
        self.assertEqual(ft.funscript_path("/m/VR/a.b/scene.mp4"), "/m/VR/a.b/scene.funscript")
        self.assertEqual(ft.funscript_path("/m/scene"), "/m/scene.funscript")


if __name__ == "__main__":
    unittest.main()
