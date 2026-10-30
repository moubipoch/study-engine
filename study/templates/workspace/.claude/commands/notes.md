---
description: Transcribe image notes (photos of handwritten notes, plots, whiteboards) so they become searchable and citable
argument-hint: [course]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

1. Find the images: run `uv run study sources <course> --json` (or loop over `uv run study courses --json`
   if no course was given). Pick:
   - sources whose `meta.needs_transcription` is true (image notes)
   - scanned PDFs: sources with `chunks == 0` but `images > 0`. Transcribe these page by page; a
     long one may need several runs.
2. For each one:
   - **View it** with Read, using the image path from `uv run study show '[@<course>:<key>#p1]' --json`.
   - **Write a faithful transcription** to `data/tmp/note_<key>.md`:
     - text and math in LaTeX
     - diagrams described in words: axes, curves, labels, what they show
     - unreadable parts marked `[illegible]`
     - no additions or interpretation
   - **Store it** with `uv run study note-text <course> <key> data/tmp/note_<key>.md`.
3. **Report** what was transcribed. Mention anything important that stood out, e.g. "plot shows the
   GSP share by gender diverging at high reserve prices".
