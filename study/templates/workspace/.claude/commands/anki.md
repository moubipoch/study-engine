---
description: Help with the Anki cards you struggle with (flagged/leeches): explain them intuition-first and propose better cards, edited only after you confirm
argument-hint: <course> [leeches | flagged | card <id>]
allowed-tools: Bash(uv run study:*), Read, Edit(./data/tmp/**)
---

Tutor the user through their difficult Anki cards. Arguments: `$ARGUMENTS`.

## 1. Get fresh data
- Run `uv run study anki pull <course> --json`.
  - If Anki isn't reachable, say so: the user needs Anki open with AnkiConnect. Then continue with the
    last pulled state, if any.
- Run `uv run study anki struggling <course> --json`. With `card <id>`, look only at that card
  (`uv run study cards list <course> --json`).
- Run `uv run study mastery <course> --json` to see whether the struggling cards cluster in a weak
  topic.

## 2. For each struggling card (at most 5 per run; flagged ones first)
1. **Diagnose why it's hard.** Look at lapses, short intervals and the card's wording. Typical causes:
   - the card asks two things at once
   - it relies on rote memory where an idea is missing
   - its wording is ambiguous
   - the prerequisite concept itself is shaky
2. **Explain the idea** in the CLAUDE.md schema, briefly: intuition, then a small example, then the
   formal statement, then the pitfall that likely causes the lapses. Ground it with the card's `cite`
   (`uv run study show '<cite>' --context 1 --json`) and cite it.
3. **Propose a better card or split,** shown as before → after. For example: split into 2 atomic cards,
   add a "why" card, add a concrete example, or rephrase as cloze-style recall.
4. **Give a memory hook if one helps:** a mnemonic, a picture, or a link to something the user already
   knows from another course (use `uv run study search <other-course> …` and cite it).

## 3. Only after the user explicitly says yes to a proposed change
- **Edit a card:** run `uv run study cards edit <id> --front "…" --back "…"`. Its review history in Anki
  is kept.
- **Add split or extra cards:** write YAML and run `uv run study cards add <course> <file>`.
- Then run `uv run study anki push <course>`.

Never change cards without that confirmation. If the user just wants explanations, give explanations.

## 4. Wrap up
- Which topic these cards point to.
- One suggestion: a `/practice <course> --topic "<topic>"` session or a digest section to re-read.
