# study-engine

An exam-prep system for [Claude Code](https://claude.com/claude-code). Your courses' slides, lecture
recordings (transcribed), exercises and past exams become a study setup where Claude knows the whole
course, quizzes you the way the exam does, remembers what you know in an Obsidian vault, and tells you
which topic earns the most points next.

It runs on your Claude subscription: no API key. The Python CLI (`study`) is deterministic tooling
(ingest, search, transcription, spaced repetition, readiness); Claude Code is the reasoning layer, driven
by slash commands.

```
coursesync ──► study sync ──► your course folders ──► Claude Code sessions ──► knowledge vault (git)
 (fetch new      (ingest,        <Course>/Claude:        /check  /wrap           Concepts/ (mastery from
  recordings,     transcribe,     slides, transcripts,    /baseline              your answers), exam
  Moodle, exams)  index)          past exams, CLAUDE.md   /exam-model            models, readiness
```

## How it works
1. **Fetch.** [coursesync](https://github.com/moubipoch/coursesync) pulls new lecture recordings
   (video.ethz.ch, Zoom), Moodle files, course websites, git repos and past exams (VIS/VMP/AMIV exam
   collections) into one folder per course. `study sync` runs it and ingests what's new.
2. **Ingest.** Recordings are transcribed locally with faster-whisper (GPU if available), slides are
   read page by page (with images for equations), code by function. Everything is searchable (BM25 +
   embeddings) with machine-checkable citations like `[@ml:L03-slides#p12]` or `[@ml:L03-rec#t=00:41:10]`.
3. **Study folders.** `study pack` writes `<Course>/Claude/`: the whole course by week (slides +
   transcripts), past exams, and a `CLAUDE.md` that turns Claude Code opened there into a tutor for that
   course: intuition first, grounded in the material, honest feedback.
4. **Knowledge vault.** An Obsidian vault (a git repo) where every concept is a node with your mastery
   (only from your answers), misconceptions, your own understanding, typed links across courses and review
   dates. Each course has an **exam model** (topics × share of points × question styles, built from the
   past exams) and a **readiness** page: expected score per topic and the next best actions.

## In a course folder
| Command | What it does |
|---|---|
| (start) | due reviews first, then the topic with the most points to gain |
| `/exam-model` | build what the exam rewards from past exams + lecturer hints in the recordings |
| `/baseline` | mid-semester start: one exam-style question per lectured topic → your starting map |
| `/check` | exam-style quiz on today's material, with confidence; wrong-but-sure = misconception |
| `/wrap` | update concept nodes, session note and readiness; commit + push the vault |
| `/readiness`, `/gaps` | where you stand; what's missing, ranked by exam points |

In the workspace (CLI or Claude Code): `/sync`, `/brief` (one-screen lecture overview) + `/followup`,
`/digest`, `/ask`, `/quiz`, `/exercise` (code exercises compiled and tested), `/parse-exams`, `/practice`,
`/grade`, `/cards` + `/anki` (Anki via AnkiConnect), `/plan`. There is also a local GUI: `study ui`.

## Setup
```bash
uv tool install "coursesync @ git+https://github.com/moubipoch/coursesync"
git clone https://github.com/moubipoch/study-engine && cd study-engine
uv run study init ~/Uni/study          # your private workspace (never commit it: course material!)
cd ~/Uni/study
# edit study.yaml (vault, where your course folders live) and courses/<slug>/course.yaml per course
uv sync                                 # add `--extra gpu` style CUDA libs if you have an NVIDIA GPU
coursesync init && coursesync run --dry-run   # configure where your courses publish material
uv run study sync                       # fetch + ingest + build the course folders
uv run study vault init                 # create the knowledge vault (then `git init` it, privately)
uv run study timer install              # optional: daily background sync
```
Then open a course folder (`<Course>/Claude`) in Claude Code, accept the trust prompt once, and start with
`/exam-model` and `/baseline`.

Requirements: Python 3.12 (uv), Claude Code, Google Chrome (coursesync's login window), optionally an
NVIDIA GPU for fast transcription, `g++` for C++ exercises, Anki + AnkiConnect for flashcards, Obsidian with
the Dataview plugin for the vault.

## Principles
- **Grounded:** every claim cites the slide or recording minute it comes from; unsourced = "(general knowledge)".
- **Honest mastery:** only answers count, never reading. Readiness says how much of it is backed by tests.
- **Exam dates are never guessed;** only from the official exam plan in `course.yaml`.
- **Your data stays yours:** the workspace and the vault are local/private; this repo contains code only.
  Course material is the lecturers': don't publish it.

## License
MIT
