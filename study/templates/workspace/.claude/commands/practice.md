---
description: Start a practice session (unseen and weak questions first) and tell the user where to answer
argument-hint: <course> [n=3] [week N] [topic "<name>"] [kind <kind>]
allowed-tools: Bash(uv run study:*), Read
---

Start a practice session. Arguments: `$ARGUMENTS`.

1. **Build the command** from the arguments:
   `uv run study practice <course> -n <n> [--lecture N] [--topic "<name>"] [--kind <kind>] --json`.
2. **If it fails because the bank is empty** for that filter, tell the user and offer `/quiz` (theory)
   or `/exercise` (code) to create questions first. Don't silently generate them: that spends usage.
3. **Otherwise, show the user:**
   - the session id
   - the path of `answer.md` (they can answer there, or in the GUI's Practice tab)
   - for code questions, the `src/` folder to edit, plus the test command they can run themselves:
     `uv run study run-tests <id> --session <session>`
   - the question statements, as written: no hints, no solutions
4. End with: *"When you're done, run `/grade <course> <session>`, or press Submit in the GUI."*
