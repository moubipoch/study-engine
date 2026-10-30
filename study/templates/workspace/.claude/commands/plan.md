---
description: Build the study plan (steady or exam-countdown mode), export .ics, and on confirmation push the blocks to the user's planner
argument-hint: [days=7] [winter]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Make the study plan. Arguments: `$ARGUMENTS`.
- `days=N` sets the length. The default is 7.
- `winter` means: also show whether the winter-break window in `courses/planner.yaml` is set, and plan
  inside it.

## 1. Respect the user's existing calendar (if the planner connector is available)
- If you have the user's planner connector (tools like `get_calendar` / `get_day`), read the next N
  days.
- Write a JSON file `data/tmp/busy.json` as `{"YYYY-MM-DD": busy_minutes_during_study_hours}`. Count
  only events that overlap the study window given by `day_start` and the hours in
  `courses/planner.yaml`.
- **Exam dates in the planner are not a source.** Exam dates come only from `course.yaml`. If the
  planner shows an exam date that `course.yaml` lacks, or that differs from it, **report the
  mismatch** and suggest the user add it to `course.yaml`. Change nothing.

## 2. Build
- Run `uv run study plan --days N [--busy data/tmp/busy.json] --json`.
- **Show the user:**
  - the plan per day, as a compact table: time, course, task, command
  - the warnings: courses without exam dates are planned in "steady mode"
  - the backlog hours per course
- **Explain in two lines** why the order is what it is: priority × backlog × exam urgency, with
  fair-share spreading.
- Mention the `.ics` path, which imports into any calendar.

## 3. Push to the planner: only after the user explicitly says yes
- Ask: *"Push these N study blocks to your planner?"*
- On yes, use the planner connector's add-event tool, one event per block:
  - title `<COURSE>: <task>`
  - the block's start and end (Europe/Zurich)
  - the command in the description
- **Never create or modify exams**, routines or anything that isn't a study block from this plan.
- If the connector isn't available in this session, say so. The `.ics` file is the fallback.

## 4. Offer the first step
Offer to start the first block right away, e.g. run its `/digest` or `/practice` command.
