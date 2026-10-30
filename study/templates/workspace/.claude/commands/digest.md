---
description: Write intuition-first lecture digests (only missing or stale ones), with glossary, key results and likely exam questions
argument-hint: <course> [week numbers… | next N]   (default: next 2)
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Write lecture digests for a course. Arguments: `$ARGUMENTS`.
- The first word is the course slug.
- Then either explicit week numbers (`3 5`), or `next N`. The default is `next 2`: the first two missing or
  stale weeks.

## 0. Decide what to do (cheap)
- Run `uv run study todo <course> --json`. Only `missing` or `stale` weeks need work. If the user named a
  week that is `fresh`, say so and skip it unless they explicitly asked to regenerate.
- Run `uv run study courses --json` and read this course's `style` block. Follow it. For example, a programming course is
  code-first: the **Formal** part is a short compilable C++ snippet, and pitfalls are about memory and
  compiler behaviour.
- Run `uv run study topics show <course> --json`. If there are approved topics, use their exact names in
  **Connections** and as concept names where they fit.

## 1. Read the lecture, per week
- Run `uv run study bundle <course> --lecture N --max-tokens 25000 --json`. If `truncated`, repeat with
  `--offset <next_offset>` until you've seen everything.
- **Images:** each chunk lists page or frame images. View, with Read, only the ones you need: an equation
  or diagram that the text extraction garbles or omits. That's usually 3–8 per lecture, never all of them.
- **Recordings:** the transcript holds the lecturer's *explanations and emphasis*, which is the best
  source for intuition and for what will be on the exam ("this is important", "typical mistake"). Cite
  its timestamps for that.
- **Connections across courses:** at most 2 calls of `uv run study search <other-course> "<concept>" -k 3 --json`.
  Only cite another course if a result truly matches; otherwise say it's (general knowledge).

## 2. Write the digest to `data/tmp/digest_<course>_L<NN>.md` in exactly this structure
```markdown
# W<N>: <lecture title, as the course names it>

## Overview
2–4 sentences: what this lecture is about, why it matters, and where it sits in the course (what it
builds on, what builds on it).

## Concepts
### <Concept name>
**Intuition**: plain words, *why* it works or why it exists.
**Example**: small and concrete. Numbers, or a tiny program for code courses.
**Formal**: definition, theorem with its assumptions, or code. LaTeX: `$...$` inline, `$$...$$` display.
Code goes in fenced blocks with a language.
**Pitfalls**: what students typically get wrong.
**Connections**: other weeks or courses, e.g. "builds on W2 (linking)" or "same idea as the Bellman
recursion in the optimal-control course [@ctrl:…]".

(4–8 concepts: the ones that actually matter, not one per slide.)

## Glossary
- **term**: one-line definition [@course:key#loc]

## Key results
- **Name of result/rule/idiom**: precise statement, including its assumptions [@course:key#loc]

## What an exam would ask
1. <question> *(type: explain | derive | compute | predict-output | find-bug | implement; difficulty: easy | medium | hard)* [@cite of where it's covered]
(4–8 questions in the course's exam style. No answers here: answering them is practice.)

## Gaps & uncertainties
Anything garbled, missing (e.g. a slide that refers to a demo you can't see), or that you filled in from
general knowledge.
```
Rules:
- **Code is verified.** For code courses, every non-trivial snippet in **Formal** or **Example** must
  compile. Save it as `data/tmp/snippets/<course>_L<NN>_<k>.cpp` (add a tiny `main` if needed) and run
  `uv run study run-cpp <file> --json`. That compiles with `-Wall -Wextra` plus ASan/UBSan and runs it.
  Fix anything that doesn't compile or that warns unintentionally. If a snippet deliberately shows a
  bug, say which tool catches it (e.g. "ASan reports heap-use-after-free").
- **Dynamics and control courses (style kind `dynamics`/`control`):** for each key system, make a phase portrait or bifurcation
  diagram, e.g. `uv run study viz phase -- "y" "-sin(x)"` or `uv run study viz bifurcation -- "r*x - x**3"`.
  Put `--` before the expressions. It classifies the fixed points from the Jacobian. View the PNG,
  describe what it shows, and mention the command so the user can re-run it.
- **Citations:** inline `[@course:key#loc]`, copied exactly from the bundle output. At least one per
  concept, glossary entry and key result. Never invent a location.
- **General knowledge:** anything not in the sources is tagged `(general knowledge)`.
- **Scratch files:** write only under `data/tmp/`. Plain `uv run study …` commands are pre-approved;
  avoid shell loops and pipes to other programs.
- **Format:** English, LaTeX math. Aim for understanding, not a slide transcript. A good digest is 1.5–3
  screens per concept-dense lecture.

## 3. Save
- Run `uv run study save-digest <course> <N> data/tmp/digest_<course>_L<NN>.md --json`.
- If it's rejected (bad citation, missing section), fix exactly what it reports and save again.
- Then do the next week.

## 4. Report
List one line per saved week: title, number of concepts, and any warnings. Then suggest the next step:
the next weeks to digest, or a quick quiz on what was just digested.
