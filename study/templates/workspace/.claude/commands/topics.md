---
description: Infer a course's topic list from its ingested material (and course page) and propose it for approval
argument-hint: <course> [course-page-url]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**), WebFetch(domain:vvz.ethz.ch)
---

Propose a topic list for a course. The user approves it afterwards, in the GUI or with `study topics approve`.

Arguments: `$ARGUMENTS`. The first word is the course slug; an optional second argument is the URL of the
public course catalogue page (vvz.ethz.ch).

## Steps
1. **Read the config.** `uv run study courses --json` and `uv run study topics show <course> --json`.
   Note the style profile and any existing topics. Never drop approved topics.
2. **Survey the material cheaply, in as few calls as possible:**
   - `uv run study sources <course>` to see weeks and types.
   - `uv run study bundle <course> --headings --type slides --type script --type exercise --max-tokens 20000`
     gives the whole-course outline in one call: one line per slide or section title, with its citation,
     ordered by week. Continue with `--offset` if it's truncated.
   - Open full text (`uv run study show '<cite>'`) only where a title is ambiguous.
   - Run each command on its own, with no shell loops or pipes to other programs; only plain
     `uv run study …` calls are pre-approved.
   - If a URL was given, fetch the public catalogue page (WebFetch) and use its content description.
     Don't access anything behind a login.
3. **Draft 8–20 topics** at "exam chapter" granularity: not per slide, not one giant topic. Each needs:
   - `name`: short, as the course itself names it.
   - `summary`: one line on what it covers.
   - `lectures`: the week numbers where it appears.
   - `cites`: 1–3 citations to the slides or pages that best show it, copied exactly from the CLI output.
   Mark topics that only come from the catalogue page (not yet in the material) with
   `summary: "(from course page; not yet covered by ingested material) ..."` and `cites: []`.
4. **Save the proposal.** Write `data/tmp/topics_<course>.yaml` as a YAML list, then run
   `uv run study topics propose <course> data/tmp/topics_<course>.yaml`. It rejects invalid citations;
   fix them and retry.
5. **Show the user** a compact table (topic, weeks, one-line summary). Tell them to approve or edit it in
   the GUI (course page → Topics) or with `uv run study topics approve <course> [names…]`.
   Point out anything you weren't sure about, e.g. where the material and the course page disagree.

Keep the token cost low: titles and headings first; open full text only where a title is ambiguous.
