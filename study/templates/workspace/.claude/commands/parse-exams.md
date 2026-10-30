---
description: Turn a course's past exams into bank questions (with official solutions) and an exam profile used to calibrate generated questions
argument-hint: <course> [exam-key…]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Parse past exams for a course. Arguments: `$ARGUMENTS`.

## 1. Find the exams
- Run `uv run study sources <course> -t exam --json`. Pair question and solution PDFs by year or
  name, e.g. `exam-2023-final-questions` with `exam-2023-final-solutions`.
- If exam keys were given, only do those. Otherwise do at most 2 exams per run, newest first (usage
  limits). Skip exams already parsed: check `uv run study q list <course> --json` for
  `source: past-exam:<key>`.

## 2. Read the exams
- Run `uv run study bundle <course> --key <exam-key> --max-tokens 20000 --json`.
- View page images (Read) where the text extraction garbles formulas, figures or tables. Exams are
  formula-heavy, so look at the images for any problem whose text is unclear.

## 3. Turn each sub-question into a bank question
Use `data/tmp/q/<exam>-<n>/`, in the format from /quiz:
- **Front matter:**
  - `source: past-exam:<exam-key>`
  - `cites: ['[@course:<exam-key>#pN]']`, the page the question is on
  - `kind`, `difficulty` (your honest estimate) and `points`, the official points if printed
  - `lecture` and `topic`: map the question to the week or topic it tests, using
    `uv run study search`
- **`solution.md`:**
  - If an official solution exists, use it and cite its page.
  - If not, write one and mark it at the top as `(solution written by Claude, not official)`.
  - In both cases, end with a rubric whose points sum to the official points.
- **Keep the exam's own wording.** Don't "improve" questions, so the practice matches the real exam.

Add with `uv run study q add <course> <dirs…> --json`, fixing anything it rejects.

## 4. Exam profile
Write `data/tmp/exam_profile_<course>.yaml` and save it with
`uv run study exam-profile <course> data/tmp/exam_profile_<course>.yaml`:
```yaml
exams_analyzed: [exam-2023-final-questions, ...]
format: {duration_min: 120, total_points: 100, aids: "closed book, 1 A4 formula sheet", parts: 5}
question_types: {derive: 0.35, compute: 0.3, explain: 0.2, mcq: 0.15}   # share of points
difficulty: {easy: 0.3, medium: 0.5, hard: 0.2}
recurring_topics: ["...", "..."]     # what comes up every year, with weeks
typical_verbs: ["show that", "sketch the phase portrait", "classify the fixed points", ...]
notes: "anything a student should know: e.g. multiple-choice has negative marking"
```
Merge with the existing profile (`uv run study exam-profile <course>`) instead of overwriting it.

## 5. Report
How many questions were added per exam, the profile highlights (format, recurring topics), and what
to practise first given the exam date in course.yaml. If the date is missing, say so.
