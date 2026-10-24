import json
import os
import tempfile
import unittest

import filterTagger as ft


SAVED = [{"id": "61", "name": "FunGen", "mode": "SCENES",
          "object_filter": {"interactive": {"modifier": "EQUALS", "value": "false"},
                            "tags": {"modifier": "EXCLUDES",
                                     "value": {"depth": 0, "excluded": [], "items": [{"id": "t9", "label": "FunGen"}]}}},
          "find_filter": {"q": None}},
         {"id": "62", "name": "Other", "mode": "SCENES", "object_filter": {}, "find_filter": None},
         {"id": "63", "name": "Studios", "mode": "SCENES",
          "object_filter": {"performers": {"modifier": "INCLUDES",
                                           "value": {"excluded": [], "items": [{"id": "p1", "label": "P"}]}},
                            "tags": {"modifier": "EXCLUDES",
                                     "value": {"depth": 0, "excluded": [], "items": [{"id": "t9", "label": "FunGen"}]}}},
          "find_filter": {"q": None}}]


def scene(sid, tags=(), path="/media/x/{sid}.mp4"):
    return {"id": sid, "tags": [{"id": t} for t in tags], "files": [{"path": path.format(sid=sid)}]}


class FakeStash:
    """Answers the handful of queries the plugin makes and records writes.
    `matching` is the set of scene ids the saved filter currently selects;
    tagging a scene removes it from the set, as a filter excluding the tag
    would."""

    def __init__(self, matching=(), tags=("FunGen",), saved=SAVED, performers=()):
        self.scenes = {s["id"]: s for s in list(matching) + list(performers)}
        # the "Studios" filter (a performers criterion) selects this set
        self.sets = {None: {s["id"] for s in matching}, "performers": {s["id"] for s in performers}}
        self.tags = {n: f"id:{n}" for n in tags}
        self.saved = saved
        self.writes = []
        self.queries = []
        self.page_size = 100

    def call(self, query, variables=None):
        self.queries.append((query, variables))
        v = variables or {}
        if "findSavedFilters" in query:
            return {"findSavedFilters": self.saved}
        if "findTags" in query:
            tid = self.tags.get(v["n"])
            return {"findTags": {"tags": [{"id": tid, "name": v["n"]}] if tid else []}}
        if "tagCreate" in query:
            self.tags[v["i"]["name"]] = f"id:{v['i']['name']}"
            return {"tagCreate": {"id": self.tags[v["i"]["name"]]}}
        if "findScenes" in query:
            f = v["f"]
            ids = sorted(self.sets["performers" if "performers" in f else None], key=int)
            if "id" in f:
                ids = [i for i in ids if int(i) == f["id"]["value"]]
            ff = v.get("ff") or {}
            n, p = ff.get("per_page", 25), ff.get("page", 1)
            page = ids[(p - 1) * n:p * n] if n > 0 else ids
            return {"findScenes": {"count": len(ids), "scenes": [self.scenes[i] for i in page]}}
        if "sceneUpdate" in query:
            i = v["i"]
            self.writes.append(i)
            s = self.scenes.get(i["id"])
            if s is not None:
                s["tags"] = [{"id": t} for t in i["tag_ids"]]
                if "id:FunGen" in i["tag_ids"]:
                    for ids in self.sets.values():
                        ids.discard(i["id"])  # the filters exclude the tag
            return {"sceneUpdate": {"id": i["id"]}}
        raise AssertionError("unexpected query " + query)


def setUpModule():
    ft.log = lambda level, msg: None


def cfg(**kw):
    c = ft.load_config({})
    c.update(kw)
    return c


class SavedFilter(unittest.TestCase):
    def test_found_by_name(self):
        st = FakeStash()
        f = ft.saved_filter(st, "FunGen")
        self.assertEqual(f.scene_filter, {"interactive": False,
                                          "tags": {"modifier": "EXCLUDES", "value": ["t9"], "excludes": [], "depth": 0}})
        self.assertEqual(f.find_filter, {})

    def test_name_match_ignores_case_when_unique(self):
        self.assertEqual(ft.saved_filter(FakeStash(), "fungen").name, "FunGen")

    def test_missing_filter_names_the_setting(self):
        with self.assertRaises(ft.FilterError) as e:
            ft.saved_filter(FakeStash(), "Nope")
        self.assertIn("Nope", str(e.exception))

    def test_empty_name_refused(self):
        with self.assertRaises(ft.FilterError):
            ft.saved_filter(FakeStash(), "")

    def test_setting_holds_a_comma_separated_list(self):
        self.assertEqual(ft.filter_names(" FunGen, Studios ,, "), ["FunGen", "Studios"])
        self.assertEqual(ft.filter_names(""), [])
        self.assertEqual(ft.filter_names(None), [])

    def test_all_named_filters_are_loaded_in_order(self):
        fs = ft.saved_filters(FakeStash(), "Studios, FunGen")
        self.assertEqual([f.name for f in fs], ["Studios", "FunGen"])

    def test_one_missing_name_stops_the_lot_and_is_named(self):
        with self.assertRaises(ft.FilterError) as e:
            ft.saved_filters(FakeStash(), "FunGen, Nope")
        self.assertIn("Nope", str(e.exception))

    def test_no_names_refused(self):
        with self.assertRaises(ft.FilterError):
            ft.saved_filters(FakeStash(), " , ")


class Hook(unittest.TestCase):
    def test_matching_scene_gets_the_tag_and_keeps_its_others(self):
        st = FakeStash(matching=[scene("7", tags=["id:VR"])])
        r = ft.run_hook(st, cfg(), ft.saved_filter(st, "FunGen"), "id:FunGen", {"id": 7, "type": "Scene.Create.Post"})
        self.assertEqual(st.writes, [{"id": "7", "tag_ids": ["id:VR", "id:FunGen"]}])
        self.assertEqual(r, "tagged")

    def test_scene_outside_the_filter_is_left_alone(self):
        st = FakeStash(matching=[scene("7")])
        r = ft.run_hook(st, cfg(), ft.saved_filter(st, "FunGen"), "id:FunGen", {"id": "8", "type": "Scene.Update.Post"})
        self.assertEqual(st.writes, [])
        self.assertEqual(r, "no match")

    def test_hook_query_adds_the_scene_id_to_the_filter(self):
        st = FakeStash(matching=[scene("7")])
        ft.run_hook(st, cfg(), ft.saved_filter(st, "FunGen"), "id:FunGen", {"id": "7"})
        q, v = [x for x in st.queries if "findScenes" in x[0]][0]
        self.assertEqual(v["f"]["id"], {"modifier": "EQUALS", "value": 7})
        self.assertFalse(v["f"]["interactive"])
        self.assertEqual(v["ff"]["per_page"], 1)

    def test_funscript_on_disk_blocks_the_tag(self):
        # Stash only learns of the funscript at the next scan; until then the
        # scene still matches "not interactive" and would be queued again.
        with tempfile.TemporaryDirectory() as d:
            video = os.path.join(d, "s.mp4")
            open(os.path.join(d, "s.funscript"), "w").close()
            st = FakeStash(matching=[scene("7", path=video)])
            r = ft.run_hook(st, cfg(), ft.saved_filter(st, "FunGen"), "id:FunGen", {"id": "7"})
            self.assertEqual(st.writes, [])
            self.assertEqual(r, "has a funscript already")
            st = FakeStash(matching=[scene("7", path=video)])
            ft.run_hook(st, cfg(skipWithFunscript=False), ft.saved_filter(st, "FunGen"), "id:FunGen", {"id": "7"})
            self.assertEqual(len(st.writes), 1)

    def test_already_tagged_scene_is_not_written_again(self):
        st = FakeStash(matching=[scene("7", tags=["id:FunGen"])])
        r = ft.run_hook(st, cfg(), ft.saved_filter(st, "FunGen"), "id:FunGen", {"id": "7"})
        self.assertEqual(st.writes, [])
        self.assertEqual(r, "already tagged")

    def test_no_scene_id_is_a_noop(self):
        st = FakeStash()
        self.assertEqual(ft.run_hook(st, cfg(), ft.saved_filter(st, "FunGen"), "id:FunGen", {}), "no scene")

    def test_filter_with_its_own_id_criterion_is_refused(self):
        saved = [{"id": "1", "name": "FunGen", "mode": "SCENES",
                  "object_filter": {"id": {"modifier": "EQUALS", "value": {"value": 3}}}}]
        st = FakeStash(saved=saved)
        with self.assertRaises(ft.FilterError):
            ft.run_hook(st, cfg(), ft.saved_filter(st, "FunGen"), "id:FunGen", {"id": "3"})


class HookAnyFilter(unittest.TestCase):
    def test_scene_in_the_second_filter_only_is_tagged_and_the_filter_named(self):
        st = FakeStash(matching=[scene("7")], performers=[scene("8")])
        fs = ft.saved_filters(st, "FunGen, Studios")
        r, name = ft.run_hook_any(st, cfg(), fs, "id:FunGen", {"id": "8"})
        self.assertEqual((r, name), ("tagged", "Studios"))
        self.assertEqual([w["id"] for w in st.writes], ["8"])

    def test_scene_in_no_filter(self):
        st = FakeStash(matching=[scene("7")], performers=[scene("8")])
        fs = ft.saved_filters(st, "FunGen, Studios")
        self.assertEqual(ft.run_hook_any(st, cfg(), fs, "id:FunGen", {"id": "9"}), ("no match", None))
        self.assertEqual(st.writes, [])

    def test_scene_in_both_filters_is_written_once(self):
        st = FakeStash(matching=[scene("7")], performers=[scene("7")])
        fs = ft.saved_filters(st, "FunGen, Studios")
        self.assertEqual(ft.run_hook_any(st, cfg(), fs, "id:FunGen", {"id": "7"}), ("tagged", "FunGen"))
        self.assertEqual(len(st.writes), 1)


class TagAll(unittest.TestCase):
    def test_union_of_filters_tags_each_scene_once(self):
        st = FakeStash(matching=[scene("1"), scene("2")], performers=[scene("2"), scene("3")])
        fs = ft.saved_filters(st, "FunGen, Studios")
        n = ft.run_all(st, cfg(), fs, "id:FunGen")
        self.assertEqual(n, 3)
        self.assertEqual(sorted(w["id"] for w in st.writes), ["1", "2", "3"])

    def test_tags_every_match_even_as_the_result_set_shrinks(self):
        # the filter excludes the tag, so each write removes a scene from the
        # query: paging live would skip every other page
        st = FakeStash(matching=[scene(str(i)) for i in range(1, 251)])
        n = ft.run_all(st, cfg(), [ft.saved_filter(st, "FunGen")], "id:FunGen")
        self.assertEqual(n, 250)
        self.assertEqual(sorted(int(w["id"]) for w in st.writes), list(range(1, 251)))

    def test_skips_tagged_and_scripted(self):
        with tempfile.TemporaryDirectory() as d:
            video = os.path.join(d, "s.mp4")
            open(os.path.join(d, "s.funscript"), "w").close()
            st = FakeStash(matching=[scene("1", path=video), scene("2", tags=["id:FunGen"]), scene("3")])
            n = ft.run_all(st, cfg(), [ft.saved_filter(st, "FunGen")], "id:FunGen")
            self.assertEqual(n, 1)
            self.assertEqual([w["id"] for w in st.writes], ["3"])


class Config(unittest.TestCase):
    def test_defaults_and_blank_means_default(self):
        c = ft.load_config({"filterName": "", "tagName": None, "skipWithFunscript": False})
        self.assertEqual(c["filterName"], "FunGen")
        self.assertEqual(c["tagName"], "FunGen")
        self.assertFalse(c["skipWithFunscript"])
        self.assertTrue(ft.load_config({})["skipWithFunscript"])

    def test_ensure_tag_creates_when_missing(self):
        st = FakeStash(tags=())
        self.assertEqual(ft.ensure_tag(st, "FunGen"), "id:FunGen")
        self.assertEqual(ft.ensure_tag(st, "FunGen"), "id:FunGen")
        self.assertEqual(sum("tagCreate" in q for q, _ in st.queries), 1)


class Manifest(unittest.TestCase):
    def test_yml_declares_hook_and_task(self):
        with open(os.path.join(os.path.dirname(ft.__file__), "filterTagger.yml")) as f:
            yml = f.read()
        for s in ("Scene.Create.Post", "Scene.Update.Post", "mode: all", "mode: hook",
                  "filterName:", "tagName:", "skipWithFunscript:", "apiKey:"):
            self.assertIn(s, yml)


if __name__ == "__main__":
    unittest.main()
