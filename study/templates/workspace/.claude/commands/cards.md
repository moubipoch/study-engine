---
description: Make good Anki flashcards from a course's digests (or material) and sync them to Anki
argument-hint: <course> [weeks…] [n=15 per week]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Create flashcards. Arguments: `$ARGUMENTS`.
- The first word is the course slug.
- Then optionally week numbers and `n=<cards per week>` (default about 15).

## 1. Source material
- **Prefer digests:** `courses/<course>/digests/L<NN>.md`. They already hold intuition, glossary and key
  results, with citations. If a week has no digest, use
  `uv run study bundle <course> --lecture N --max-tokens 15000 --json`.
- **Don't duplicate:** check `uv run study cards list <course> --json`.

## 2. Card-writing rules (minimum-information principle)
- **One fact or idea per card.** Split anything with "and".
- **Ask for understanding, not slide trivia.**
  - Prefer: "Why does `Point p3();` not create an object?", "What assumption fails if V is only
    positive semi-definite?", "Bellman equation for a finite-horizon problem with stage cost g?"
  - Avoid: "What is on slide 12?"
- **Mix in:** definitions (from the glossary); theorem statements *with their assumptions* (key
  results); "why"/intuition cards; and for code courses, short predict-the-output snippets (≤ 6 lines).
- **Short answer side:** 1–3 lines. LaTeX with `$…$` / `$$…$$`, which is converted to Anki MathJax
  automatically. Code goes in backticks or fenced blocks.
- **Every card gets one `cite`:** the best supporting location, copied from the digest or CLI output.
  Use the approved topic name as `topic`, and the week as `lecture`.

## 3. Save and sync
1. Write `data/tmp/cards_<course>.yaml`:
   ```yaml
   - front: "Why must a const or reference member be initialised in the initializer list?"
     back: "The ctor body runs after all members exist; you can't assign to a const and a reference must bind at creation."
     cite: "[@sim:L04-slides#p23]"
     topic: "Classes: design, data hiding, members"
     lecture: 4
   ```
2. Run `uv run study cards add <course> data/tmp/cards_<course>.yaml --json`. It rejects uncited or
   badly cited cards; fix them and re-add.
3. Run `uv run study anki push <course> --json`.
   - If Anki isn't reachable, run `uv run study anki export <course>` instead, and tell the user to
     import the `.apkg` (File → Import). Re-importing later updates cards instead of duplicating them.

## 4. Report
How many cards per week and topic, and whether they're in Anki or need importing.
