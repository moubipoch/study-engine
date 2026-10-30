---
description: Pull everything new for all courses in one go (repos, websites, video.ethz.ch, Moodle, Zoom), transcribe, refresh the study folders, and report
argument-hint: [course …]   (default: all courses)
allowed-tools: Bash(uv run study sync:*), Bash(uv run study briefs:*), Bash(uv run study status:*), Read
---

Run the sync and report. Arguments: `$ARGUMENTS` (optional course slugs to limit it to).

1. Run `uv run study sync $ARGUMENTS` **in the background** (it can take a while: new recordings are
   downloaded and transcribed on the GPU) and wait for it to finish.
   - Everything is deterministic (coursesync): sources without a login (git repos, public or
     password-stored websites) run directly; sources behind the university login use the saved login
     of the sync's own Chrome profile.
   - If that login has expired, **a Chrome window opens on the ETH login page** and the sync waits up
     to 15 minutes. Tell the student right away: "a Chrome window opened: log in there (tick *keep me
     signed in*); the sync continues by itself". Never type credentials or solve bot checks yourself,
     and don't drive any browser: the script does all of it.
2. When it's done, read `data/logs/last_sync.md` and report briefly, per course: new recordings
   (week), new files, new past exams; anything under *Problems* or *Needs your ETH login*, with what
   to do about it.
3. Suggest next steps only where useful, e.g. "week 7 is in: open the course's Claude folder and ask for the
   week 7 overview", or `/parse-exams <course>` for new past exams. Nothing else.
