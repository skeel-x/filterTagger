#!/usr/bin/env python3
"""Filter Tagger: a Stash plugin that adds a tag to every scene a saved
filter selects.

You describe the scenes that should carry the tag with an ordinary saved
scene filter in Stash; the plugin adds the tag to any scene that matches,
as soon as the scene is created or changed (hook) or across the library
(task). It never removes the tag: put "tag is not X" into the filter and
whatever takes the tag off keeps the scene out.

Standard library only.
"""
import json
import os
import sys
import urllib.request

DEFAULTS = {
    "filterName": "FunGen",
    "tagName": "FunGen",
    "skipWithFunscript": True,
    "apiKey": "",
}
BOOLEAN = ("skipWithFunscript",)


def log(level, msg):
    # Stash reads plugin logs off stderr, one prefixed line at a time
    print(f"\x01{level}\x02{msg}", file=sys.stderr, flush=True)


def progress(fraction):
    # Stash reads "\x01p\x02<float>" as the task's progress, 0 to 1
    log("p", f"{min(1.0, max(0.0, fraction)):.4f}")


def load_config(stored):
    cfg = dict(DEFAULTS)
    for k, v in (stored or {}).items():
        if v not in (None, ""):
            cfg[k] = v
    for k in BOOLEAN:
        cfg[k] = bool(cfg[k])
    for k in ("filterName", "tagName"):
        cfg[k] = str(cfg[k]).strip() or DEFAULTS[k]
    return cfg


class FilterError(Exception):
    """The saved filter cannot be used: missing, or a criterion the plugin
    does not understand. Nothing is tagged then; tagging on a partial
    filter would tag too much."""


# ------------------------------------------------------------------ stash

class Stash:
    def __init__(self, conn):
        scheme = conn.get("Scheme") or "http"
        port = conn.get("Port") or 9999
        self.url = f"{scheme}://localhost:{port}/graphql"
        cookie = conn.get("SessionCookie") or {}
        self.headers = {"Content-Type": "application/json"}
        if cookie.get("Name"):
            self.headers["Cookie"] = f"{cookie['Name']}={cookie['Value']}"

    def use_api_key(self, key):
        """Authenticate with an API key instead of the task's session cookie,
        which Stash expires after an hour."""
        self.headers.pop("Cookie", None)
        self.headers["ApiKey"] = key

    def call(self, query, variables=None):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = urllib.request.Request(self.url, data=body, headers=self.headers)
        with urllib.request.urlopen(req, timeout=600) as r:
            out = json.loads(r.read())
        if out.get("errors"):
            raise RuntimeError(out["errors"][0].get("message"))
        return out["data"]


SAVED_FILTERS = "{findSavedFilters(mode:SCENES){id name mode object_filter find_filter{q}}}"
TAG_BY_NAME = """query($n:String!){findTags(tag_filter:{name:{value:$n,modifier:EQUALS}},
  filter:{per_page:2}){tags{id name}}}"""
TAG_CREATE = "mutation($i:TagCreateInput!){tagCreate(input:$i){id name}}"
SCENES = """query($f:SceneFilterType,$ff:FindFilterType){findScenes(scene_filter:$f,filter:$ff){
  count scenes{id tags{id} files{path}}}}"""
SCENE_UPDATE = "mutation($i:SceneUpdateInput!){sceneUpdate(input:$i){id}}"


def ensure_tag(stash, name):
    """The id of the tag called name, created if it does not exist."""
    hits = stash.call(TAG_BY_NAME, {"n": name})["findTags"]["tags"]
    if hits:
        return hits[0]["id"]
    made = stash.call(TAG_CREATE, {"i": {"name": name}})["tagCreate"]
    log("i", f"created tag {name}")
    return made["id"]


# ----------------------------------------------------- saved filter shape

# Stash stores a saved filter's criteria in the shape its web UI edits them
# in, not in the SceneFilterType that findScenes takes. The UI converts on
# the way out; this is that conversion for scene filters. A criterion the
# plugin does not know is an error, never silently dropped.

def _get(m, *path):
    for key in path:
        if not isinstance(m, dict) or key not in m:
            return None
        m = m[key]
    return m


def _modifier(c):
    m = c.get("modifier")
    if not isinstance(m, str):
        raise FilterError(f"modifier is {type(m).__name__}, expected string")
    return m


def _ids(items, what):
    if items is None:
        return []
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or "id" not in item:
            raise FilterError(f"{what}[{i}] has no id")
        out.append(str(item["id"]))
    return out


def _int(c):
    out = {"modifier": _modifier(c)}
    if out["modifier"] == "IS_NULL":
        return out
    out["value"] = int(_get(c, "value", "value") or 0)
    if _get(c, "value", "value2") is not None:
        out["value2"] = int(c["value"]["value2"])
    return out


def _string(c):
    out = {"modifier": _modifier(c)}
    if out["modifier"] == "IS_NULL":
        return out
    v = c.get("value")
    if not isinstance(v, str):
        raise FilterError(f"value is {type(v).__name__}, expected string")
    out["value"] = v
    return out


def _simple_string(c):
    v = c.get("value")
    if not isinstance(v, str):
        raise FilterError(f"value is {type(v).__name__}, expected string")
    return v


def _bool(c):
    v = _simple_string(c).lower()
    if v not in ("true", "false"):
        raise FilterError(f"value {v!r} is not a bool")
    return v == "true"


def _multi(c):
    out = {"modifier": _modifier(c)}
    if out["modifier"] == "IS_NULL":
        return out
    out["excludes"] = _ids(_get(c, "value", "excluded"), "value.excluded")
    items = _get(c, "value", "items")
    if items is None and isinstance(c.get("value"), list):
        items = c["value"]
    out["value"] = _ids(items, "value.items")
    return out


def _hier(c):
    out = _multi(c)
    if out["modifier"] != "IS_NULL":
        depth = _get(c, "value", "depth")
        out["depth"] = int(depth) if depth is not None else 0
    return out


def _ranged(c):
    """Dates and timestamps: value.value with an optional value.value2."""
    out = {"modifier": _modifier(c)}
    if out["modifier"] == "IS_NULL":
        return out
    v = _get(c, "value", "value")
    if not isinstance(v, str):
        raise FilterError("value.value is missing or not a string")
    out["value"] = v
    if _get(c, "value", "value2") is not None:
        out["value2"] = str(c["value"]["value2"])
    return out


def _phash(c):
    out = {"modifier": _modifier(c)}
    if out["modifier"] == "IS_NULL":
        return out
    v = _get(c, "value", "value")
    if not isinstance(v, str):
        raise FilterError("value.value is missing or not a string")
    out["value"] = v
    if _get(c, "value", "distance") is not None:
        out["distance"] = int(c["value"]["distance"])
    return out


RESOLUTIONS = {
    "144p": "VERY_LOW", "240p": "LOW", "360p": "R360P", "480p": "STANDARD",
    "540p": "WEB_HD", "720p": "STANDARD_HD", "1080p": "FULL_HD", "1440p": "QUAD_HD",
    "1920p": "VR_HD", "4k": "FOUR_K", "5k": "FIVE_K", "6k": "SIX_K", "8k": "EIGHT_K",
    "Huge": "HUGE",
}


def _resolution(c):
    out = {"modifier": _modifier(c)}
    if out["modifier"] == "IS_NULL":
        return out
    v = _simple_string(c)
    if v not in RESOLUTIONS:
        raise FilterError(f"unknown resolution {v!r}")
    out["value"] = RESOLUTIONS[v]
    return out


CAPTIONS = {
    "Deutsche": "de", "English": "en", "Español": "es", "Français": "fr", "Italiano": "it",
    "日本": "ja", "한국인": "ko", "Holandés": "nl", "Português": "pt", "Русский": "ru",
    "Unknown": "00",
}


def _caption(c):
    out = _string(c)
    if "value" in out:
        out["value"] = CAPTIONS.get(out["value"], out["value"])
    return out


def _stash_id(c):
    out = {"modifier": _modifier(c)}
    for src, dst in (("endpoint", "endpoint"), ("stashID", "stash_id")):
        v = _get(c, "value", src)
        if v is not None:
            out[dst] = str(v)
    return out


def _duplicated(c):
    out = {}
    for key in ("phash", "stash_id", "title", "url"):
        v = _get(c, "value", key)
        if v is not None:
            out[key] = bool(v)
    return out


def _orientation(c):
    v = c.get("value")
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise FilterError("value is not a list of strings")
    return {"value": [x.upper() for x in v]}


CRITERIA = {
    "audio_codec": _string, "bitrate": _int, "captions": _caption, "checksum": _string,
    "code": _string, "created_at": _ranged, "date": _ranged, "details": _string,
    "director": _string, "duplicated": _duplicated, "duration": _int, "file_count": _int,
    "framerate": _int, "galleries": _multi, "groups": _hier, "has_markers": _simple_string,
    "id": _int, "interactive": _bool, "interactive_speed": _int, "is_missing": _simple_string,
    "last_played_at": _ranged, "movies": _multi, "o_counter": _int, "organized": _bool,
    "orientation": _orientation, "oshash": _string, "path": _string, "performer_age": _int,
    "performer_count": _int, "performer_favorite": _bool, "performer_tags": _hier,
    "performers": _multi, "phash": _string, "phash_distance": _phash, "play_count": _int,
    "play_duration": _int, "production_date": _ranged, "rating100": _int, "resolution": _resolution,
    "resume_time": _int,
    "stash_id_endpoint": _stash_id, "studios": _hier, "tag_count": _int, "tags": _hier,
    "title": _string, "updated_at": _ranged, "url": _string, "video_codec": _string,
}


def scene_filter(objects):
    """The SceneFilterType for a saved filter's object_filter."""
    out = {}
    for key, c in (objects or {}).items():
        parse = CRITERIA.get(key)
        if parse is None:
            raise FilterError(f"criterion {key} is not supported by this plugin")
        if not isinstance(c, dict):
            raise FilterError(f"criterion {key}: value is {type(c).__name__}, expected object")
        try:
            out[key] = parse(c)
        except FilterError as e:
            raise FilterError(f"criterion {key}: {e}") from None
        except (TypeError, ValueError) as e:
            raise FilterError(f"criterion {key}: {e}") from None
    return out


def find_filter(ff):
    """Only the search text of a saved find_filter narrows the result; sort
    and paging are the plugin's own."""
    q = (ff or {}).get("q")
    return {"q": q} if q else {}


class SavedFilter:
    def __init__(self, name, scene_filter, find_filter):
        self.name = name
        self.scene_filter = scene_filter
        self.find_filter = find_filter

    def query(self, **more):
        """The scene filter with extra criteria merged in."""
        return dict(self.scene_filter, **more)


def saved_filter(stash, name):
    """The saved scene filter called name, as a query. An exact name wins;
    otherwise a case-insensitive match, if there is exactly one."""
    name = (name or "").strip()
    if not name:
        raise FilterError("no saved filter name is set")
    rows = [r for r in stash.call(SAVED_FILTERS)["findSavedFilters"] if r.get("mode") == "SCENES"]
    hits = [r for r in rows if r["name"] == name]
    if not hits:
        hits = [r for r in rows if r["name"].lower() == name.lower()]
    if len(hits) != 1:
        raise FilterError(f"saved scene filter {name!r} not found" if not hits
                          else f"saved scene filter {name!r} is ambiguous")
    row = hits[0]
    return SavedFilter(row["name"], scene_filter(row.get("object_filter")),
                       find_filter(row.get("find_filter")))


def filter_names(setting):
    """The 'Saved filter' setting as a list: comma-separated names, blanks
    dropped. Stash ANDs every criterion inside one filter, so "these
    performers or these studios" takes one filter per branch."""
    return [n.strip() for n in (setting or "").split(",") if n.strip()]


def saved_filters(stash, setting):
    """Every saved filter the setting names, in that order. One missing
    name stops the lot: tagging through the rest would quietly tag less
    than asked."""
    names = filter_names(setting)
    if not names:
        raise FilterError("no saved filter name is set")
    return [saved_filter(stash, n) for n in names]


# ---------------------------------------------------------------- tagging

def funscript_path(path):
    return os.path.splitext(path)[0] + ".funscript"


def has_funscript(scene):
    return any(os.path.exists(funscript_path(f["path"])) for f in scene.get("files") or [] if f.get("path"))


def tag_scene(stash, cfg, scene, tag_id):
    """Add the tag to the scene unless it has it, or has a funscript Stash
    has not scanned yet. Returns what happened."""
    have = [t["id"] for t in scene.get("tags") or []]
    if tag_id in have:
        return "already tagged"
    if cfg["skipWithFunscript"] and has_funscript(scene):
        return "has a funscript already"
    stash.call(SCENE_UPDATE, {"i": {"id": scene["id"], "tag_ids": have + [tag_id]}})
    return "tagged"


def run_hook(stash, cfg, filt, tag_id, ctx):
    """One scene, from the hook: tag it if the filter selects it."""
    sid = ctx.get("id")
    if sid is None:
        return "no scene"
    if "id" in filt.scene_filter:
        raise FilterError("the saved filter's own id criterion cannot be combined with the hook's scene")
    ff = dict(filt.find_filter, per_page=1)
    found = stash.call(SCENES, {"f": filt.query(id={"modifier": "EQUALS", "value": int(sid)}),
                                "ff": ff})["findScenes"]["scenes"]
    if not found:
        return "no match"
    return tag_scene(stash, cfg, found[0], tag_id)


def run_hook_any(stash, cfg, filts, tag_id, ctx):
    """The hook against several filters: the first one that selects the
    scene decides. Returns the result and that filter's name."""
    for filt in filts:
        r = run_hook(stash, cfg, filt, tag_id, ctx)
        if r != "no match":
            return r, filt.name
    return "no match", None


def select_all(stash, filt):
    """Every scene one filter selects, by paging on id."""
    scenes, page = [], 1
    while True:
        ff = dict(filt.find_filter, per_page=100, page=page, sort="id", direction="ASC")
        d = stash.call(SCENES, {"f": filt.scene_filter, "ff": ff})["findScenes"]
        scenes.extend(d["scenes"])
        if not d["scenes"] or len(scenes) >= d["count"]:
            break
        page += 1
    log("i", f"{len(scenes)} scenes match {filt.name!r}")
    return scenes


def run_all(stash, cfg, filts, tag_id):
    """Every scene any of the filters selects, from the task. The ids are
    collected first: a filter that excludes the tag loses a scene on every
    write, and paging the live result would skip past half of them. A
    scene in several filters is tagged once."""
    scenes, seen = [], set()
    for filt in filts:
        for sc in select_all(stash, filt):
            if sc["id"] not in seen:
                seen.add(sc["id"])
                scenes.append(sc)
    if len(filts) > 1:
        log("i", f"{len(scenes)} scenes match any of the {len(filts)} filters")
    tagged = 0
    for i, sc in enumerate(scenes):
        r = tag_scene(stash, cfg, sc, tag_id)
        if r == "tagged":
            tagged += 1
        else:
            log("d", f"scene {sc['id']}: {r}")
        progress((i + 1) / len(scenes))
    log("i", f"tagged {tagged} scenes with {cfg['tagName']}")
    return tagged


def main():
    payload = json.loads(sys.stdin.read())
    stash = Stash(payload.get("server_connection") or {})
    args = payload.get("args") or {}
    try:
        stored = (stash.call("{configuration{plugins}}")["configuration"]["plugins"]
                  or {}).get("filterTagger") or {}
    except Exception:
        stored = {}
    cfg = load_config(stored)
    if cfg.get("apiKey"):
        stash.use_api_key(cfg["apiKey"])
    mode = args.get("mode") or "hook"

    try:
        filts = saved_filters(stash, cfg["filterName"])
    except FilterError as e:
        # a warning per hook would flood the log while the filter is not
        # yet saved; the task is where someone looks for the reason
        log("w" if mode == "all" else "d",
            f"{e}: nothing tagged. Set the plugin's 'Saved filter' to the name of a saved "
            "scene filter, or several names separated by commas.")
        print(json.dumps({"output": "ok"}))
        return
    tag_id = ensure_tag(stash, cfg["tagName"])

    if mode == "all":
        run_all(stash, cfg, filts, tag_id)
    else:
        ctx = args.get("hookContext") or {}
        r, name = run_hook_any(stash, cfg, filts, tag_id, ctx)
        if r == "tagged":
            log("i", f"scene {ctx.get('id')}: tagged {cfg['tagName']} (matches {name!r})")
    print(json.dumps({"output": "ok"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("e", f"{type(e).__name__}: {e}")
        if getattr(e, "code", None) == 401:
            log("e", "Stash rejected the request. Long tasks outlive the hour-long session "
                     "Stash gives a plugin; set an API key in the plugin settings "
                     "(Settings -> Security -> API key) and run the task again.")
        print(json.dumps({"error": str(e)}))
