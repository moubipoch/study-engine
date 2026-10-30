---
description: Create verified C++ (or Python) exercises (predict-output, find-bug, fix-performance, refactor-with-templates, implement, race-and-speedup) with tests
argument-hint: <course> <kind> [week | topic "<name>"] [n=2]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Create code exercises that are **compiled and tested before the user ever sees them**.
Arguments: `$ARGUMENTS`.

## 0. Environment
- Run `uv run study tools --json`. Don't write exercises that need a missing tool. For example, MPI
  exercises only if `mpi` is ok; race exercises need `tsan`.
- Run `uv run study courses --json` and read the style block. For a programming course the kinds are typically predict-output,
  find-bug, fix-performance, refactor-with-templates, implement, and race-and-speedup.
- Ground the exercise in the week's material: read the digest if one exists, or
  `uv run study bundle <course> --lecture N`. Reuse the course's own examples (the Simpson integrator,
  `Point`, `Penna`…) where it fits, and cite them.

## 1. Folder layout: `data/tmp/q/<name>/`
```
q.md            front matter (as in /quiz) + task statement; kind = one of the code kinds
                grading: {mode: unit}            # or {mode: io}; add profile: tsan for races,
                                                 # flags: ["-fopenmp"] for OpenMP
solution.md     explanation of the bug/fix/design: memory + compiler-level intuition, then '## Rubric'
src/            what the student gets (starter / buggy / slow / to-implement code)
ref/            reference solution: same file names as src/
tests/test.cpp  (unit) `#include "solution.hpp"`; asserts; exit 0 = pass. No main() in src/ for unit mode.
tests/cases.yaml (io) [{stdin: "...", stdout: "..."}] for whole programs with a main()
```

## 2. Recipes per kind
- **predict-output:** use rubric grading (`grading: rubric`). Put the program in the statement.
  Before writing the solution, compile and run it with
  `uv run study run-cpp data/tmp/q/<name>/prog.cpp --json` and use its **actual** output. The rubric
  rewards explaining *why* (evaluation order, copy vs move, overload resolution…), not just the output.
- **find-bug:** the starter has one realistic bug: off-by-one, dangling reference, iterator
  invalidation, missing virtual destructor, signed/unsigned, or a use-after-move. The tests must
  **fail** on the starter (ideally with a sanitizer hit) and pass on the reference.
- **fix-performance:** `grading: {mode: unit, profile: bench}`. The test asserts correctness and also a
  time bound that the slow starter misses and the reference meets. Measure both with
  `uv run study run-cpp … --profile bench --repeat 5`. The solution explains the cause: cache misses,
  allocation in a loop, pass-by-value copies, virtual calls in hot loops…
- **refactor-with-templates:** the starter has duplicated code for 2–3 types. The tests instantiate
  the template with several types, including one the duplicated code doesn't support.
- **implement:** the starter has signatures plus `TODO`s and must fail the tests.
- **race-and-speedup:** `profile: tsan`. The starter has a data race, and the tests plus TSan must
  catch it. The solution covers atomic/mutex/reduction trade-offs and expected speedup (Amdahl).
  For OpenMP use `flags: ["-fopenmp"]`.

## 3. Verify, then add
- Run `uv run study q add <course> data/tmp/q/<name> --json`. It compiles with `-Wall -Wextra` plus
  sanitizers, requires the reference to pass with **zero warnings**, and requires the starter to fail
  (for find-bug, implement and race kinds). If it rejects, fix and retry. Never hand out an exercise
  that didn't pass `q add`.
- **Python courses** (numerics in Python): use `grading: {mode: py}`:
  - `src/solution.py` holds the stub, `ref/solution.py` the reference, and `tests/test_*.py` the
    pytest tests (numpy, scipy and matplotlib are available).
  - Run scripts with `uv run study run-py <file>`.

## 4. Report
List the new ids and kinds. Offer `/practice <course> --kind <kind>`. Don't reveal the solutions.
