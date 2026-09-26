# SnapSort

**A private agent that keeps your personal documents organised and never sends your data anywhere.**

SnapSort watches your Downloads, Screenshots and Desktop folders. It works out what each file is, pulls out what
matters, files it, reminds you about deadlines, locks secrets in an encrypted vault, and asks you before doing
anything sensitive. It runs 100% offline on **Gemma 4 E2B and E4B** through Ollama.

Built for the Google DeepMind × GDG Hyderabad hackathon, Problem Statement 5 (local-first agents).

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

`scripts/demo_reset.sh` resets everything. `scripts/drop.sh jio|airtel|lab` drops a file in live.
The full flow is in [docs/demo-script.md](docs/demo-script.md).

## Tests

```bash
pytest               # ~115 fast tests, no model needed (a scripted fake stands in)
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
