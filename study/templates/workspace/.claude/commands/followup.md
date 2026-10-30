---
description: Answer a follow-up question about one lecture week in depth, grounded in its slides and recording
argument-hint: <course> <week> <question>
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

The user read the short overview of a lecture week and now asks a follow-up to go deeper.
Arguments: `$ARGUMENTS`. The first word is the course slug, the second the week number, the rest is the
question. Later messages in this conversation are more follow-ups about the same week: keep going.

## Each answer
1. **Context (first answer only):** Read `courses/<course>/briefs/L<NN>.md` if it exists (what the
   user has already seen).
2. **Find the material for this question:**
   `uv run study search <course> "<question>" --lecture N -k 8 --json`, and without `--lecture` if the
   answer may be in another week or the script. For more around a hit:
   `uv run study show '<cite>' --context 1 --json`. Check the **recording transcript** hits: they hold
   the lecturer's explanation and examples. View (Read) a slide image when the answer is a figure or
   an equation. If it helps, look at how past exams ask it: `-t exam`.
3. **Answer** at the depth the question needs, intuition first:
   - why / intuition in plain words,
   - a small worked example,
   - the formal statement or derivation (LaTeX) or code,
   - the common pitfall, if there is one.
   Not every answer needs every part: a short question gets a short answer. Go as deep as asked.
4. **Cite** inline `[@course:key#loc]`, copied exactly from the tool output; unsourced claims get
   `(general knowledge)`; if the material doesn't cover it, say so.
5. **End** with one line offering a next step, e.g. a quick question to check understanding, or the
   natural next thing to ask. If the user answers a check question, give honest, specific feedback.

Make all tool calls before writing the answer and don't narrate them: the user sees only the answer.
