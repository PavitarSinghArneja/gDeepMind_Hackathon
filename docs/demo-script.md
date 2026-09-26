# SnapSort demo (about 3.5 minutes)

The pitch follows Problem Statement 5 and its bar: a sense-decide-act-check loop, clear boundaries for human
handoff, local state, and offline error recovery. Each part is shown live, not described.

**Watch it:** https://youtu.be/H9jOFs8JhB4

**Recorded version:** `demo_video/record_pitch.py` drives the real app in Chrome at 1920x1080 and really kills
SnapSort mid-work; `demo_video/build_pitch.py` cuts the waiting, adds the voice-over and writes
`snapsort_pitch.mp4` + `snapsort_pitch.srt`. Narration lives in `demo_video/pitch.json`.

## Setup

1. `scripts/demo_reset.sh`, then `python -m snapsort --headless` to sort the backlog (about 6 minutes on 16 GB).
2. Put a real lab report in `mock/` (not watched) to drop in live.
3. Start the app: `.venv/bin/python -m snapsort`, open http://127.0.0.1:8765. Warm both Gemma models.

## Script

| Time | Section | Show | Say |
|---|---|---|---|
| 0:00 | The problem | Title slide | Private files sit in Downloads. A chatbot still makes you ask about every file. SnapSort is an agent that sorts, checks and protects them on its own, offline, on Gemma 4. |
| 0:20 | The product | Header pills, summary, Needs you | E2B sorts, E4B reads, EmbeddingGemma searches. Zero outside connections. 22 files already sorted: 11 on its own, 11 waiting for me. |
| 0:40 | The bar | Slide with the four requirements | The challenge's bar. Let's meet each one, live. |
| 0:55 | **1/4 Loop, not an arrow** | Drop the lab report; open its pipeline | Sense, decide with two Gemma models, check every value against the document, plan, apply privacy rules, act, confirm on disk, roll back if it didn't land. |
| 1:25 | 1/4 continued | Highlight the blurry bill and the injection note in Needs you | Checks change outcomes: an unconfirmed amount waits for me; a note ordering the AI to delete files is flagged, and no delete tool exists. |
| 1:40 | **2/4 Human handoff** | Policy slide (runs on its own / asks first / always waits / impossible) | One policy file draws the line. Medical, ID, failed checks and under 80% confidence always wait. Delete, share, upload are impossible. |
| 2:00 | 2/4 continued | Lab report: Lock in vault, Undo bar | One clear button per decision; encrypted on disk with a local key; every action can be undone. |
| 2:10 | **3/4 Local state** | Move the Airtel bill to Finance/Telecom; rule appears | One SQLite file: task queue with checkpoints, action journal, reminders, learned rules. |
| 2:25 | **4/4 Offline recovery** | Drop 3 files, `kill -9` mid-work, restart | No warning. |
| 2:35 | 4/4 continued | Feed: "Restarted after an interruption", "Resuming from the 'extract' stage", "Applied your rule #1"; filter "airtel" | Resumes from the last finished step, reconciles half-done actions, nothing lost, and the rule survived the crash. If the model stops, files wait without using retries. |
| 2:55 | Everyday use | Ask "what's my wifi password?" (blurred, cited); export expenses; Mark paid then Undo | Plain-language answers with sources; spreadsheet export with unverified amounts marked; reminders; undo everything. |
| 3:15 | Results | Stats slide | 22 files in 106 s on E2B, 0 outside connections, crash recovered, 3 unsafe actions stopped, 9/10 search, 120 tests. |
| 3:25 | Close | Repo URL | Senses, decides, acts and checks, offline on Gemma 4. Keeps its own state, recovers from failure, knows when to ask. |

## Live-presentation notes

- Kill command: `pkill -9 -if "m snapsort"`, restart with `.venv/bin/python -m snapsort`, then refresh the page.
- Other drops: `scripts/drop.sh jio|airtel|lab`, or drag any file onto the page.
- If the model misbehaves live, play `demo_video/snapsort_pitch.mp4`.
