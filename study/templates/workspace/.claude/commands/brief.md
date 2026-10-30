---
description: Short lecture overview (one screen) — the big picture and key ideas of a week, as a starting point for follow-up questions
argument-hint: <course> <week numbers… | missing>   (e.g. ml 3, ml 1 2 3, ml missing)
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Write short lecture overviews. Arguments: `$ARGUMENTS`. The first word is the course slug, then week
numbers, or `missing` for every week whose overview is missing or stale
(`uv run study briefs <course> --json`). Skip weeks that are already `fresh` unless the user named them.

The user wants a **short understanding first** and will ask follow-ups to go deeper. So this is not a
digest: no full derivations, no exhaustive coverage. It should take 3–5 minutes to read.

## Per week
1. Read the material: `uv run study bundle <course> --lecture N --max-tokens 25000 --json` (with
   `--offset` if `truncated`). It has the slides and the **recording transcript**: use the transcript
   to see what the lecturer actually emphasised, their examples and analogies, and any "this is
   important / exam" remarks. View (Read) at most 2–3 slide images, only if a key figure or equation
   is unreadable in the text.
2. Write `data/tmp/brief_<course>_L<NN>.md`:

```markdown
# W<N>: <lecture title>

**In one sentence:** <what this lecture is about and why it matters>.

## The big picture
3–5 sentences: the problem this week solves, the main idea of the solution, and where it sits in the
course (builds on …, needed for …). Plain words.

## Key ideas
- **<Idea>**: 1–2 sentences of intuition, a formula only if it *is* the idea ($…$). [@course:key#loc]
(4–6 bullets, in the order they build on each other; plain words first, at most one short formula per bullet)

## What the lecturer stressed
- <a point, example or warning from the recording> [@course:key-rec#t=HH:MM:SS]
(2–4 bullets; skip the section if the week has no recording)

## Go deeper
1. <a follow-up question the user could ask to understand more, e.g. "Why does … need …?">
(5–7 questions, from intuition to exam-level; phrased as the user would ask them, no answers)
```

Rules: citations exactly as given by the tools (at least one per key idea); anything not from the
material gets `(general knowledge)`; math in LaTeX; English; **under ~500 words** (short is the point: depth comes from the follow-ups).

3. Save: `uv run study save-brief <course> N data/tmp/brief_<course>_L<NN>.md --json`. Fix what it
   reports if rejected.

## Report
One line per saved week (title). Nothing else.
