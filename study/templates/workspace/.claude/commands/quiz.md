---
description: Generate exam-style questions (with reference solutions and rubrics) for a course's weeks or topics
argument-hint: <course> [weeks… | topic "<name>"] [n=5]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Add exam-style questions to a course's question bank. Arguments: `$ARGUMENTS`.
- The first word is the course slug.
- Then week numbers, or `topic "<name>"`, and optionally `n=<count>` (default 5).

## 1. Calibrate (cheap)
- Run `uv run study courses --json` and read the course's `style` block. Its `exercise_kinds` say which
  kinds fit this course.
- Run `uv run study exam-profile <course>`. If a profile exists, match its formats, point scales,
  question types and difficulty mix. If there is none, say so and use the style profile.
- Run `uv run study q list <course> --json` and **don't duplicate** existing questions. Fill gaps instead:
  weeks or topics with few questions, kinds not covered yet.

## 2. Ground the questions in the material
- **If a digest exists:** read `courses/<course>/digests/L<NN>.md`. Its "What an exam would ask" section is
  a starting point.
- **Otherwise:** use `uv run study bundle <course> --lecture N --max-tokens 15000 --json`.
- **Check specifics:** for anything specific (a definition, a theorem's assumptions), confirm the wording
  with `uv run study search` and cite it.

## 3. Write each question as a folder `data/tmp/q/<short-name>/`
`q.md`:
```markdown
---
title: <short title>
topic: <approved topic name if there is one>
lecture: <week>
kind: explain | derive | prove | compute | formulate | mcq | short   (code kinds: use /exercise)
difficulty: easy | medium | hard
points: <integer>
source: generated
cites: ['[@course:key#loc]']        # where the material covers this
---
<Question statement. LaTeX for math. Self-contained: give every quantity the student needs.>
```
`solution.md`: a complete worked reference solution, intuition first where it helps, then the
rigorous steps. It ends with:
```markdown
## Rubric
- (2) <a concrete, checkable step: "states that V must decrease along trajectories">
- (1) <...>
```
Rubric points must sum to `points`. Each rubric line should be gradeable by a reader of the answer,
not vague ("good understanding").

**Course-specific:**
- **proof-based courses:** include "prove it from scratch" items, and make "which assumption fails if…" a
  rubric step.
- **control / optimisation courses:** formulation drills, e.g. "write the Bellman equation / the QP for this system",
  plus small numeric ones.
- **dynamics courses:** fixed-point classification and bifurcation questions with concrete systems.
- **ML / probabilistic courses:** derivations tied to algorithms (posterior updates, ELBO, acquisition functions). For coding
  questions (small numpy implementations), use `grading: {mode: py}`: `src/solution.py` holds a
  stub, `ref/solution.py` the reference, and `tests/test_solution.py` the pytest tests. `q add` checks
  that the reference passes and the stub fails.
- **dynamics courses:** for "sketch the phase portrait / classify the fixed points", verify your reference answer
  with `uv run study viz phase -- "<x'>" "<y'>" --json` before writing the solution.
- **ais:** don't generate exam questions. It's a project-only course; say so and stop.

## 4. Add (validated)
- Run `uv run study q add <course> data/tmp/q/<name1> data/tmp/q/<name2> … --json`.
- It rejects rubric/points mismatches and bad citations. Fix exactly what it reports and re-add.

## 5. Report
- A table: id, week, kind, difficulty, points.
- Offer to start practising: `/practice <course>`, or "Practice" in the GUI.
- **Never show the solutions** in the report: they're for grading.
