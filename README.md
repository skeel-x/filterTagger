# Filter Tagger

A [Stash](https://github.com/stashapp/stash) plugin that adds a tag to every
scene a saved scene filter selects. You describe the scenes with an ordinary
saved filter in Stash's own UI; the plugin tags any scene that matches, as
soon as it is created or changed, and across the whole library on demand.

The typical use is a queue for another program. Say a tool watches Stash for
scenes tagged `FunGen`, writes a funscript for each, and removes the tag when
it is done. Save a filter such as

* studio is one of your favourites, or performer is one of your favourites,
* interactive is false (no funscript yet),
* tags does not include `FunGen`,

and name it `FunGen`. From then on every new scene from those studios gets
the tag the moment its studio is set; once the script exists and the tag is
gone, the scene no longer matches and nothing happens to it again.

The plugin only ever adds the tag. It never removes it, so a scene that
should drop out of the queue has to drop out of the filter: put "tag is not
X" into the filter and whatever removes the tag keeps the scene out.

Stash ANDs every criterion inside one saved filter, and its filter UI has
no "or" between criteria. "Scenes with these performers, or any scene from
these studios" is therefore two saved filters. The plugin's "Saved filter"
setting takes several names separated by commas, and a scene in any of
them gets the tag. Each filter needs its own "tag is not X" and whatever
else keeps finished scenes out.

## Requirements

* Stash v0.24 or later (plugin sources). Saved filters are read in the
  shape Stash v0.24 and later store them.
* Python 3 as Stash's plugin interpreter. Standard library only.

## Install

Settings > Plugins > Available plugins > Add source:

```
https://skeel-x.github.io/filterTagger/main/index.yml
```

Install "Filter Tagger", then set the plugin's settings: the name of the
saved filter and the tag to add (both default to `FunGen`). Run the task
"Tag all matching scenes now" once to tag what already matches; the hook
takes care of everything after that.

## How it works

* **Hook** (`Scene.Create.Post`, `Scene.Update.Post`): the plugin asks Stash
  for the hooked scene through the saved filter plus `id = that scene`. One
  result means the scene matches, and it gets the tag (its other tags are
  kept). No result, nothing happens.
* **Task** "Tag all matching scenes now": every scene any of the filters
  selects is tagged, once. The ids are collected before any write, because
  a filter that excludes the tag loses a scene on each write and paging the
  live result would skip past half of them.
* With several filters the hook tries them in the order given and the
  first one that selects the scene decides; the log line names it.
* A scene is created before it is scraped, so its studio and performers
  usually arrive in a later update; that is why the hook also listens to
  updates. The plugin's own write fires an update too, but by then the scene
  has the tag and either no longer matches or is skipped as already tagged.
* **Funscript on disk**: Stash only learns that a scene is interactive at
  the next scan. If the other program removes the tag before that scan, the
  removal itself fires the hook and the scene still looks like "not
  interactive". So a scene whose video has a `.funscript` beside it is not
  tagged (setting, default on).

### Which criteria are understood

Everything the scene filter UI offers in Stash v0.32: tags, performers,
studios, groups, galleries, performer tags, performer favourite, performer
age and count, rating, play count and duration, o counter, resume time,
interactive and interactive speed, organized, path, title, details,
director, code, URL, checksum, oshash, phash and phash distance, audio and
video codec, bitrate, framerate, duration, file count, resolution,
orientation, captions, date, production date, created and updated at,
last played at, tag
count, is missing, has markers, stash id endpoint, duplicated. The saved
filter's search text ("q") is honoured too; its sort and page size are
not needed.

A criterion the plugin does not understand stops it with a warning in the
log, and nothing is tagged. Tagging through a filter with one criterion
dropped would tag too much. The same goes for a name in the setting that
matches no saved filter: tagging through the others would tag too little.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| Saved filter | `FunGen` | Name of the saved scene filter, or several names separated by commas (a scene in any of them is tagged). An exact name wins; otherwise a case-insensitive match if it is unique. One missing name stops the plugin. |
| Tag to add | `FunGen` | Created if it does not exist. |
| Skip scenes with a funscript beside the video | on | See above. |
| Stash API key | empty | Optional; lets a long task outlive the hour-long plugin session. |

## Development

```
python3 -m unittest -v
python3 build.py        # writes _site/main/filterTagger.zip and index.yml
```

The GitHub Pages workflow runs the tests and publishes the plugin source on
every push to `main`.

## Licence

AGPL-3.0, like Stash.
