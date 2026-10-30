---
description: Project mode (AI in the Sciences and other project-only courses): literature digests, experiment planning, code review; no mock exams
argument-hint: <course> [lit "<topic or paper>" | plan "<idea>" | review <path>]
allowed-tools: Bash(uv run study:*), Read, Glob, Grep, Edit(./data/tmp/**), WebSearch, WebFetch
---

Project mode for `$ARGUMENTS`. This course has **no exam**, so never generate mock exams here.

- **`lit "<topic>"`:**
  1. Search the course material first: `uv run study search <course> "<topic>" --json`. Its lectures
     and tutorials define the methods vocabulary (PINNs, neural operators, …).
  2. Then search the web for 3–6 key papers. Prefer the original papers and well-cited surveys.
  3. For each paper, write a short digest:
     - problem → method → key idea (intuition first) → what it assumes → results → limitations
     - how it connects to the course (cite the lecture)
     - code availability
  4. Save the digests to `data/tmp/lit_<topic>.md` and offer to ingest them as a note
     (`uv run study ingest <course> <file> --type note`).
- **`plan "<idea>"`:** turn the idea into an experiment plan:
  - hypothesis
  - baseline (from the course tutorials where possible)
  - datasets
  - metrics
  - ablations
  - compute estimate: the user has an RTX 4060 with 8 GB
  - week-by-week milestones, and the risks
- **`review <path>`:** read the user's project code (Read/Glob/Grep only) and review it:
  - correctness: shapes, gradients, train/test leakage, seeds
  - experiment hygiene: configs, logging, reproducibility
  - performance
  - what to test
  Concrete suggestions with file:line. Don't rewrite their project unless they ask.
