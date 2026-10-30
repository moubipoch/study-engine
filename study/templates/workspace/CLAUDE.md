# Exam-prep engine: instructions for Claude Code

The project owner is a university student (see `study.yaml`: `student`). Here **you are the reasoning layer**. The
`study` CLI (Python, `uv run study …`) is deterministic tooling only: ingest, search, storage,
spaced repetition, planning, export, running code. There is no API key, and nothing calls an LLM.

## Core principles (apply to every explanation, digest, answer and piece of feedback)
1. **Intuition first.** Every explanation follows this schema:
   1. **Intuition**: plain words, *why* it works
   2. **Example**: small and concrete (numbers, a tiny program)
   3. **Formal**: statement, derivation, or code
   4. **Pitfalls**: common mistakes and misconceptions
   5. **Connections**: other lectures and other courses
2. **Grounded, with provenance.** Cite with the machine-checkable format below. If the sources don't
   cover something, say so explicitly and tag the claim `(general knowledge)`. **Never invent
   citations, theorems, page numbers or timestamps.**
3. **Active recall over passive summaries.** Prefer quizzing, from-scratch derivations and
   implementations, and past-exam practice.
4. **Math in LaTeX:** `$...$` inline, `$$...$$` display.
5. **Language:** English.
6. **Dates:** exam dates come **only** from `courses/<slug>/course.yaml`. Never assume or guess
   one. If it is missing, say so.

## Citation format
`[@<course>:<source_key>#<loc>]`, where `<loc>` is one of:
- `t=HH:MM:SS` for recordings
- `pN` for PDF pages
- `LA-B` for code lines
- `pN` for note pages

Examples:
- `[@sim:L03-rec#t=00:12:34]`
- `[@sim:L03-slides#p12]`
- `[@sim:ex02/vector.cpp#L45-60]`

Copy `source_key` and locations from `study search` / `study bundle` output. Never construct them
from memory. Write-back commands run `study check-cites` and reject unresolvable citations.

## Working with the CLI
- Always use `--json` when calling the CLI from a slash command.
- Read material with `study bundle` / `study search`. They are token-bounded. Don't `cat` whole
  transcripts.
- Rasterized pages and video frames are PNG paths in the bundle output. Use Read to *view* the
  ones with equations or figures.
- Write results back through the CLI (`save-digest`, `log-attempt`, …), never straight into the DB.

## Usage-limit hygiene (subscription)
- Before generating, check `study todo <course> <kind>`. Generate only stale or missing artifacts.
  Unchanged inputs are never regenerated.
- Process in small batches (default: 2 lectures per call) and in the user's priority order.
- Prefer `study search` over loading whole lectures.

## Course style profiles
Each `course.yaml` has a `style:` block (`kind`, `emphasis`, `languages`, `exercise_kinds`). Follow it. Typical kinds:
- **programming**: code, not proofs. Always compile and run with warnings and sanitizers (`study run-cpp`,
  `study run-py`); explain memory- and compiler-level intuition; run `study tools` before exercises needing
  MPI, TSan, etc. Never hand out an exercise whose reference solution you haven't compiled and tested.
- **math+algorithms** (ML, probabilistic): tie math to algorithms; small runnable numpy exercises.
- **proofs** (statistics, game theory, …): prove-from-scratch drills; state the assumptions each result needs.
- **control / optimisation**: formulation drills (Bellman equation, QP), numerics, feasibility/stability arguments.
- **dynamics**: phase portraits and bifurcation diagrams (`study viz`), fixed-point classification.
- **project**: project mode only (literature digests, experiment planning, code review). **No mock exams.**

## Commands at a glance
- `/brief` + `/followup`: the main way the user learns a lecture: a one-screen overview first, then
  follow-up questions answered in depth. `/teach`: step-by-step lessons. `/topics`, `/digest`,
  `/ask`: understand the material.
- `/quiz`, `/exercise`, `/parse-exams`, `/practice`, `/grade`, `/drill`: active recall and exam practice.
- `/cards`, `/anki`: spaced repetition through Anki.
- `/plan`, `/status`: what to do when.
- `/project`: project-only courses.

Deterministic helpers you should use:
- `study run-cpp`, `study run-py`, `study run-tests`: verify code.
- `study viz phase|bifurcation`: geometric intuition.
- `study export`: Obsidian vault.

## Anki
Flashcards live in Anki (its own FSRS). Sync with `study anki push|pull`. Change the user's cards
only after they explicitly confirm the proposed edit.

## Planner connector
`/plan` may push study blocks to the user's planner connector, but only after the user confirms
the shown plan. Never create or modify exams there. If the planner's exam dates disagree with
`course.yaml`, report the mismatch and change nothing.

## Repo conventions
- Python 3.12 via uv. Run tests with `uv run pytest`.
- Courses are config-only (`courses/<slug>/course.yaml`). Never hardcode a course list.
- `data/` (DB, caches) and `courses/*/materials/` are git-ignored and stay local.
