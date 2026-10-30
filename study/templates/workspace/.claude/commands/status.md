---
description: Where am I? Coverage, digests, questions, cards, mastery, due items and exam dates per course, plus the best next step
allowed-tools: Bash(uv run study:*)
---

1. Run `uv run study status --json` and `uv run study plan --days 1 --json`.
2. Show a compact table per course: exam, recording hours, digests fresh/total, questions, cards,
   due, mastery.
3. **Point out gaps:**
   - missing exam dates (never guess them)
   - stale digests
   - courses with no questions yet
   - past exams not parsed yet (exam sources exist but no `past-exam:` questions)
4. **End with today's top 3 actions,** each with the exact command to run.
