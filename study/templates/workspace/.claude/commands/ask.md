---
description: Grounded, intuition-first answer to a course question, with checked citations
argument-hint: <course> <question>
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Answer a question about a course using only the ingested material, plus clearly labelled general knowledge.

Arguments: `$ARGUMENTS`. The first word is the course slug and the rest is the question. If no slug is
given, run `uv run study courses --json` and pick the course the question is clearly about. If that's
ambiguous, ask.

## Steps
1. **Retrieve.** Run `uv run study search <course> "<question>" --json -k 8`.
   - If the results are thin or off-topic, rephrase once: synonyms, the formal term, a code identifier.
   - For "what happened in lecture N" questions, use `uv run study bundle <course> --lecture N --json --max-tokens 12000`.
   - If a key result has an image (equation or figure), view it with Read before explaining it.
   - For more context around a hit, run `uv run study show '<cite>' --context 1 --json`.
2. **Answer** in this structure, skipping a section only if it truly doesn't apply:
   - **Intuition**: plain words, *why* it works.
   - **Example**: small and concrete (numbers, or a tiny program for programming courses).
   - **Formal**: definition, theorem, or code, in LaTeX (`$...$`, `$$...$$`) or a fenced code block.
   - **Pitfalls**: common mistakes.
   - **Connections**: related lectures in this course and in the user's other courses.
3. **Cite.** Put `[@course:key#loc]` citations inline, copied exactly from the search output. Never invent
   one. Anything the sources don't cover gets `(general knowledge)`. If the material doesn't answer the
   question at all, say so first.
4. **Verify.** Write the answer to `data/tmp/answer.md` and run `uv run study check-cites data/tmp/answer.md`.
   Fix any invalid citation before replying.
5. **Reply** with the answer, then a single line: *Want a quick recall question on this?*

Keep it focused: one to two screens, not a lecture transcript. The user prefers understanding over
memorising.
