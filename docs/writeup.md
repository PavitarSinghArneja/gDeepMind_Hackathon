# SnapSort: an offline Gemma 4 agent for the documents you'd never upload

*Sense, decide, act, check, entirely on your laptop, with a clear line for when it asks you.*

## The problem

Downloads and Screenshots folders fill up with bills, bank statements, payslips, lab reports, ID scans, WiFi passwords and OTPs. You can't find them when needed, and you shouldn't upload them to a cloud assistant. The model has to run where the documents are.

## What it does

SnapSort watches three folders. Each new file is read, classified and mined for key fields, then filed under `library/<Category>/` with a consistent name. Due dates become reminders; secrets get a vault proposal; anything sensitive or uncertain goes to a "Needs you" inbox. It never deletes and has no network tools. Its only traffic is to Ollama on `127.0.0.1`, and the UI shows the process's non-localhost connection count.

The current build runs entirely on **Gemma 4 E2B** (`gemma4:e2b` via Ollama) for triage, extraction and planning, with EmbeddingGemma for search. The code can route extraction and low-confidence escalation to E4B: set `SNAPSORT_WORK_MODEL=gemma4:e4b`. The demo did not use it.

## Architecture

```
mock/{Downloads,Screenshots,Desktop} --watchdog + backlog scan-->
  tasks (SQLite: lease, per-stage checkpoint, attempts) --worker-->
  SENSE > DEDUPE > TRIAGE > EXTRACT > CHECK > PLAN > GATE > ACT > CHECK > INDEX
  OCR     hashes   Gemma    Gemma     code    Gemma  policy journal code   FTS5+vectors
                                              +code  .yaml
  events --SSE--> FastAPI 127.0.0.1:8765 <-- UI (feed, library, inbox, ask)
  Ollama 127.0.0.1:11434 (gemma4:e2b, embeddinggemma)
```

## The agent loop in detail

1. **Sense.** PyMuPDF reads the text layer. Scanned PDFs are rendered and run through Tesseract. Blurry images are upscaled and sharpened, then OCR'd again. Password-protected and corrupt files go straight to the inbox.
2. **Dedupe.** Files with the same SHA-256 are marked as duplicates automatically. A near match (dHash distance ≤ 6 *and* ≥ 80% text similarity) is sent to you as a question.
3. **Triage.** Gemma returns type, sensitivity, whether a secret is visible, a title and a confidence score, as JSON constrained by a schema. Invalid values fall back to safe defaults: unknown sensitivity becomes `high`.
4. **Extract.** Gemma fills in per-type fields such as amount, due date, vendor and test values. Keys outside the type's spec are dropped.
5. **Check.** Deterministic verification runs (below).
6. **Plan.** Gemma picks tools from a fixed menu and gives a reason for each. Code fills in exact arguments: the folder, `<date>_<type>_<issuer>.<ext>`, and the reminder date. An unusable plan falls back to a per-type default. Learned rules apply next.
7. **Gate, act, check, index.** The gate sorts actions using the policy. Tools run through the journal. Postconditions are verified. The file is indexed.

Every stage streams to the UI as a plain sentence.

## Verification and safety

- **Grounding.** Extracted amounts, dates and reference numbers must appear in the document text after normalisation, in several numeric and month-name forms. If a value doesn't appear, the file goes to the inbox. When a file has almost no text, Gemma is asked whether the value is visible in the image.
- **Ambiguous dates.** If a date appears only as numbers with day ≤ 12, the check fails with "could be 2026-10-03 or 2026-03-10; I assumed day/month". The due date is editable in the inbox.
- **Prompt injection.** Prompts label the document as untrusted data. A pattern check flags text like "ignore previous instructions", which sends the file to the inbox. Even a fooled model can't do damage, because no delete, share or upload tool exists.
- **Allowlist gate.** Unlisted tools are kept in the plan so the gate can block and log them. Model-influenced folders that resolve outside `library/` are refused.
- **Rollback.** If a postcondition fails (a file isn't at its destination, or a reminder row is missing), the task's applied actions are undone in reverse order and the file goes to the inbox.
- **Encrypted vault.** Secrets are Fernet-encrypted with a local 0600 key. The plaintext is removed only after the ciphertext is written. Vaulting always needs approval.

## State and recovery

All state lives in one SQLite file: files, tasks, journal, inbox, reminders, rules, events and the search index.

- **Checkpoints.** Each stage's output is merged into the task's checkpoint. On resume, finished stages are skipped, so model calls are never repeated.
- **Journal before action.** Exact arguments are resolved and written as a `pending` journal row. Then the tool runs and the row becomes `applied` or `failed`. On resume, the journal decides which actions already ran.
- **Reconcile after crash.** At boot, interrupted tasks are requeued and each `pending` row is settled from the disk: a file at its destination and absent from its source counts as applied. Otherwise the recorded path reverts to the source and any half-written ciphertext is removed. The feed reports "resuming N task(s), reconciled M half-finished action(s)".
- **Model-down deferral.** If Ollama is unreachable or the model isn't pulled, the task waits 10 seconds *without spending an attempt*. Nothing is lost. Timeouts and bad JSON get one more try down the model list (the same E2B model in this build). Other failures retry with backoff, then reach the inbox after 3 attempts. If a model build rejects images, the client switches to text-only.

## Human handoff

`config/policy.yaml` draws the line:

| Runs on its own | Asks first | Never |
|---|---|---|
| file, remind, mark duplicate (all undoable) | vault, flag for review; **everything** if confidence < 0.8, a check fails, or it's an ID / prescription / lab report | delete, share, upload, send (no such tools exist) |

With E2B only, the E4B escalation is skipped, so low-confidence files go straight to you. Inbox items state the reason ("Out of range: HbA1c 7.2 (high)") and offer Approve, Edit (folder, due date) or Leave it.

**Corrections become rules.** Changing a folder, whether in the inbox or by moving a filed document, stores a rule keyed on type and issuer ("Airtel bills go to Finance/Telecom"). Rejecting an action stores a skip rule. The next match shows "Applied your rule #N". When rules conflict, the newest one wins.

## Why these technical choices

- **E2B by default.** The job is narrow (classify, fill a schema, pick from five tools) and deterministic checks catch mistakes, so the smaller model suffices and leaves memory for OCR. E4B is one environment variable away.
- **The model decides, code executes.** Gemma chooses *which* tool and *why*, and code writes paths and names. Filenames stay consistent and nothing escapes the library.
- **Schema-constrained JSON at temperature 0.** Ollama's `format` prevents most parse failures. Required-key checks catch the rest.
- **Verification in code, not another LLM pass.** String grounding is cheap, deterministic and unit-testable. It targets the worst failure: a confidently wrong amount or date.
- **SQLite for everything.** One transactional file, no daemon.
- **Remove capabilities rather than trust judgement.** A tool that doesn't exist can't be misused.

## Challenges

- **Ambiguous DD/MM dates.** Text alone can't settle "03/10", so the agent asks instead of guessing silently.
- **Similar-looking screenshots.** Perceptual hashes alone rate mostly-white screenshots as alike, so a match also requires similar text.
- **Watcher feedback.** The agent's own moves triggered watcher events. Updating the stored path *before* each move fixed this.
- [[FILL: concrete challenge from the live run, e.g. an E2B output or OCR failure and how it was handled]]
- [[FILL: second concrete challenge from the live run]]

## Results

Setup: [[FILL: machine and RAM]], Gemma 4 E2B via Ollama, WiFi off.

- Files processed: [[FILL: number of files in mock corpus]]
- Average time per file: [[FILL: avg seconds per file on M1 Pro]]
- Handled automatically: [[FILL: files auto-handled]]. Sent to inbox: [[FILL: files sent to inbox, with reasons]]
- Escalations: [[FILL: escalations count, or "none, E4B not enabled"]]
- Checks that caught model errors: [[FILL: checks that caught errors, with examples]]
- `kill -9` mid-backlog: [[FILL: tasks resumed / actions reconciled]]
- Outbound non-localhost connections: [[FILL: outbound connection count]]
- Tests: 115 passing unit tests using a scripted fake model, plus live tests against real Gemma 4 (`pytest -m live`).

## What's next

- Benchmark the E4B work model for accuracy against latency.
- Encrypt the database itself.
- Add OCR for Hindi and Telugu.
- Build a mobile version on LiteRT.
- Support real user folders with per-folder opt-in.

---

Word count: 1391
