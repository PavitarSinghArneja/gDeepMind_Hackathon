# SnapSort

**A private agent that keeps your personal documents organised and never sends your data anywhere.**

SnapSort watches your Downloads, Screenshots and Desktop folders. It works out what each file is, pulls out what
matters, files it, reminds you about deadlines, locks secrets in an encrypted vault, and asks you before doing
anything sensitive. It runs 100% offline on **Gemma 4 E2B and E4B** through Ollama.

Built for the Google DeepMind × GDG Hyderabad hackathon, Problem Statement 5 (local-first agents),
**track: Personal data**. Writeup: [docs/writeup.md](docs/writeup.md).

**▶ Demo video (3.5 min): https://youtu.be/H9jOFs8JhB4**

## Why local-first

These files are bank statements, payslips, lab reports, ID scans and WiFi passwords. That's exactly the data
that should never be uploaded. SnapSort's only network traffic is to Ollama on `127.0.0.1`.

## The agent loop


```
sense → dedupe → triage (E2B → E4B if unsure) → extract (E4B → E2B on failure) → CHECK
      → plan (model picks tools, code fills arguments) → policy gate → act (journaled) → CHECK → index
      → done, or hand off to a person
```

- **Checks before acting:** extracted amounts, dates and reference numbers must appear in the document text;
  numeric dates that could be read two ways are flagged; text addressed to an AI is flagged as a prompt injection.
- **Checks after acting:** every move, reminder and duplicate link is confirmed; if one fails, the task is rolled back.
- **State:** SQLite holds the task queue with per-stage checkpoints, an action journal, the inbox, reminders,
  learned rules and a search index.
- **Recovery:** kill the process at any point; on restart, interrupted tasks resume from their last stage and
  half-finished file moves are reconciled against the disk. If the model isn't running, files wait without losing
  retries.

## How it meets the bar

It isn't a straight arrow from input to output. Each file goes round a loop, and that loop can stop to ask a person.
The table shows where each requirement is implemented:

| Requirement | What it does | Code |
|---|---|---|
| **A loop, not an arrow** | Checks can reject the model's output, escalate from E2B to E4B, roll back actions or hand off; corrections become rules that change later runs | [`Pipeline._stages`](snapsort/agent/pipeline.py#L115), [`verifier`](snapsort/agent/verifier.py), [`rules.learn`](snapsort/agent/rules.py#L32) |
| **Local state management** | One SQLite file: leased task queue, per-stage checkpoints, write-ahead action journal, inbox, reminders, rules, search index | [`taskq.claim`](snapsort/taskq.py#L19), [`taskq.checkpoint`](snapsort/taskq.py#L41), [`tools.execute`](snapsort/tools.py#L127), [`db.py`](snapsort/db.py) |
| **Offline error recovery** | Resume from the last checkpoint after `kill -9`; settle half-finished actions from disk; wait when Ollama is down (no retry spent); fall back between models; roll back when a postcondition fails | [`taskq.recover`](snapsort/taskq.py#L80), [`tools.reconcile`](snapsort/tools.py#L204), [`taskq.defer`](snapsort/taskq.py#L58), [`llm.generate_with_fallback`](snapsort/llm.py#L109), [`tools.undo_task`](snapsort/tools.py#L177) |
| **Clear boundaries for human handoff** | `auto` / `confirm` / `never` lists, a confidence threshold and always-ask document types, checked before any tool runs | [`config/policy.yaml`](config/policy.yaml), [`policy.gate`](snapsort/agent/policy.py#L50), [`human.py`](snapsort/human.py) |

Each one is tested: `tests/test_taskq.py`, `tests/test_tools.py`, `tests/test_policy.py`, `tests/test_pipeline.py`.

## Human handoff (`config/policy.yaml`)

| Runs on its own | Asks first | Never |
|---|---|---|
| file, remind, mark duplicate (all undoable) | vault, flag for review, and **everything** when confidence < 0.8, a check failed, or it's an ID / prescription / lab report | delete, share, upload, send (no such tools exist) |

Corrections become rules: move an Airtel bill to `Finance/Telecom` once, and the next one goes there on its own.

## Run it

Requirements: macOS or Linux, Python 3.10+, [Ollama](https://ollama.com), Tesseract.

```bash
brew install ollama tesseract          # Linux: see ollama.com/download; apt install tesseract-ocr
brew services start ollama
ollama pull gemma4:e2b && ollama pull gemma4:e4b && ollama pull embeddinggemma

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/make_mock_data.py       # synthetic mock folders in ./mock
python scripts/smoke_models.py         # checks the models and times one call each
python -m snapsort                     # UI at http://127.0.0.1:8765
```

Other modes: `python -m snapsort --headless` processes everything and prints a report.
Model names can be changed with `SNAPSORT_TRIAGE_MODEL`, `SNAPSORT_WORK_MODEL` and `SNAPSORT_EMBED_MODEL`.

## Demo

Watch the pitch and live demo: **https://youtu.be/H9jOFs8JhB4**. It shows each part of the Problem Statement 5 bar live, including a real `kill -9` mid-work and the recovery.

`scripts/demo_reset.sh` resets everything. `scripts/drop.sh jio|airtel|lab` drops a file in live.
The full flow is in [docs/demo-script.md](docs/demo-script.md).

## Tests

```bash
pytest               # 120 fast tests, no model needed (a scripted fake stands in)
pytest -m live       # real Gemma 4 through Ollama
```

## Layout

| Path | What |
|---|---|
| `snapsort/sense/` | reading files (PyMuPDF, Tesseract), fingerprints, folder watcher |
| `snapsort/agent/` | schemas, prompts, checks, policy, planner, rules, the pipeline |
| `snapsort/tools.py` | the only actions the agent can take; journal, undo, crash reconciliation |
| `snapsort/human.py` | inbox, approvals, corrections |
| `snapsort/search.py` | keyword + semantic search, answers that must cite files |
| `snapsort/api/server.py`, `web/` | local API and UI |
| `docs/superpowers/specs/` | design spec with every scenario handled |

## Limits

Works on the mock folders in this repo (not your real Downloads). Tesseract is set up for English. The database
itself isn't encrypted; vaulted files are (Fernet, key stored locally).
