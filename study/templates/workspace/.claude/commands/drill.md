---
description: Prove-it-from-scratch drill (proof-based courses): one theorem, assumptions first, step-by-step feedback
argument-hint: <course> [topic or theorem name]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Run a proof drill. Arguments: `$ARGUMENTS`.

1. **Pick the result.**
   - If the user named one, find it with `uv run study search <course> "<name>" --json`.
   - Otherwise take the weakest topic from `uv run study mastery <course> --json` and choose one of its
     key results (from `courses/<course>/key_results.md`, or via search). Prefer results that appear in
     the past exams (`uv run study exam-profile <course>`).
2. **Pose it like an exam,** with the citation but **without** the proof:
   - (a) State the result precisely.
   - (b) List *every* assumption it needs.
   - (c) Prove it from scratch.
   - (d) Give a counterexample that shows one assumption can't be dropped.
3. **Wait for the user's attempt.** Then grade (a)–(d) step by step against the source proof, which
   you look up with `uv run study show '<cite>' --context 2`:
   - ✓ / ~ / ✗ per step, with the exact gap ("you used continuity of V but never assumed it").
   - For a missing step, give a hint first and the full step only if asked.
   - Finish with the intuition: *why* the proof works, in two sentences.
4. **To record it in the bank,** save the drill as a rubric question (as in /quiz, with `kind: prove`),
   then `uv run study log-attempt` with the score. FSRS will bring it back.
