---
description: Grade a practice session step by step against reference solutions (code is compiled and tested) and log the results
argument-hint: <course> <session>
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Grade a practice session. Arguments: `$ARGUMENTS` (course slug, session id).

## For each question in `uv run study session <course> <session> --json`
1. **Skip if already graded.** Skip questions with `attempted > 0`, unless the user asked to regrade.
2. **Load the reference.** Run `uv run study q show <id> --solution --json` for the solution and rubric.
3. **For code questions,** run `uv run study run-tests <id> --session <session> --json` and save its
   output to `data/tmp/grade/<id>.tests.json`.
   - Test results are **facts**: if the tests fail, the "it works" rubric steps get 0, whatever the
     explanation says.
   - Read the student's code in `code_dir`, and judge the explanation and the design separately.
4. **Grade each rubric line independently.** Full, partial or zero points, each with one sentence of
   evidence quoted or paraphrased from the answer.
   - Be fair: accept equivalent formulations and correct alternative methods.
   - Don't award points for things not written.
   - An empty answer scores 0.
5. **Write feedback** to `data/tmp/grade/<session>_<id>.md`:
   - **Score:** points / max.
   - **What you got right:** brief.
   - **Step-by-step:** each rubric step with ✓ / ~ / ✗ and why.
   - **The key idea you missed,** if any, intuition first. Cite where the material covers it
     (`[@course:key#loc]`, from the question's cites or `uv run study search`).
   - **For code:** compiler warnings or sanitizer findings, and what they mean at the memory or
     compiler level.
   - **One follow-up drill:** a small question that targets the gap.
6. **Log it:**
   - Write the steps JSON to `data/tmp/grade/<id>.steps.json` as `[{step, points, max, comment}]`.
   - Run `uv run study log-attempt <id> <session> --score <points/max> --points <points>
     --steps data/tmp/grade/<id>.steps.json --feedback data/tmp/grade/<session>_<id>.md
     [--tests data/tmp/grade/<id>.tests.json]`.

## Then report
- A table: question, score, the one-line main issue.
- The weakest topic in this session.
- The single most useful next step, e.g. re-read a cited passage, `/practice <course> --topic X`, or a
  specific `/exercise`.

Keep the tone of a good TA: direct, specific, encouraging, never vague.
