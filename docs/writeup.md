# SnapSort: an offline Gemma 4 agent for the documents you'd never upload

*Sense, decide, act, check, entirely on your laptop, with a clear line for when it asks you.*

## The problem

Downloads and Screenshots folders fill up with bills, bank statements, payslips, lab reports, ID scans, WiFi passwords and OTPs. They're hard to find and too private for a cloud assistant. The model has to run where the documents are.

## What it does

SnapSort watches three folders. Each new file is read, classified, mined for key fields and filed under `library/<Category>/` with a consistent name. Due dates become reminders, secrets get a vault proposal, and anything sensitive or uncertain goes to a "Needs you" inbox. It never deletes and has no network tools. Its only traffic is to Ollama on `127.0.0.1`, and the UI shows the process's non-localhost connection count.

This build runs entirely on **Gemma 4 E2B** (`gemma4:e2b` via Ollama) for triage, extraction and planning, with EmbeddingGemma for search. Extraction and low-confidence escalation can route to E4B via `SNAPSORT_WORK_MODEL=gemma4:e4b`; the demo did not use it.

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

1. **Sense.** PyMuPDF reads the text layer; scanned PDFs are rendered and OCR'd with Tesseract. Blurry images are upscaled, sharpened and re-OCR'd. Password-protected and corrupt files go straight to the inbox.
2. **Dedupe.** Identical SHA-256 files are auto-marked duplicates. A near match (dHash distance ≤ 6 *and* ≥ 80% text similarity) becomes a question for you.
3. **Triage.** Gemma returns type, sensitivity, secret visibility, title and confidence as schema-constrained JSON. Invalid values fall back to safe defaults: unknown sensitivity becomes `high`.
4. **Extract.** Gemma fills per-type fields such as amount, due date, vendor and test values. Off-spec keys are dropped.
5. **Check.** Deterministic verification (below).
6. **Plan.** Gemma picks tools from a fixed menu, giving reasons. Code fills exact arguments: folder, `<date>_<type>_<issuer>.<ext>`, reminder date. Unusable plans fall back to per-type defaults; learned rules apply next.
7. **Gate, act, check, index.** The policy gate sorts actions, tools run through the journal, postconditions are verified, and the file is indexed.

Each stage streams to the UI as plain sentences.

## Verification and safety

- **Grounding.** Extracted amounts, dates and reference numbers must appear in the normalised text (several numeric and month-name forms), or the file goes to the inbox. For near-textless files, Gemma checks whether the value is visible in the image.
- **Ambiguous dates.** A numeric-only date with day ≤ 12 fails the check with "could be 2026-10-03 or 2026-03-10; I assumed day/month", and the due date is editable in the inbox.
- **Prompt injection.** Prompts mark the document untrusted. A pattern check flags "ignore previous instructions" and sends the file to the inbox. A fooled model still can't delete, share or upload: those tools don't exist.
- **Allowlist gate.** The gate blocks and logs unlisted tools. Model-influenced folders outside `library/` are refused.
- **Rollback.** If a postcondition fails (file not at destination, reminder row missing), applied actions are undone in reverse order and the file goes to the inbox.
- **Encrypted vault.** Secrets are Fernet-encrypted with a local 0600 key; plaintext is removed only after the ciphertext is written. Vaulting always needs approval.

## State and recovery

One SQLite file holds files, tasks, journal, inbox, reminders, rules, events and the search index.

- **Checkpoints.** Each stage's output merges into the task checkpoint; on resume, finished stages (and their model calls) are skipped.
- **Journal before action.** Exact arguments are written as a `pending` row; the tool runs and the row becomes `applied` or `failed`. Resume uses the journal to decide what already ran.
- **Reconcile after crash.** At boot, interrupted tasks are requeued and each `pending` row is settled from disk: a file at its destination and absent from its source counts as applied; otherwise the recorded path reverts to the source and any half-written ciphertext is removed. The feed shows "resuming N task(s), reconciled M half-finished action(s)".
- **Model-down deferral.** If Ollama is down or the model isn't pulled, the task waits 10 seconds *without spending an attempt*. Timeouts and bad JSON get one more try down the model list (the same E2B model in this build). Other failures back off, reaching the inbox after 3 attempts. If a model build rejects images, the client switches to text-only.

## Human handoff

`config/policy.yaml` draws the line:

| Runs on its own | Asks first | Never |
|---|---|---|
| file, remind, mark duplicate (all undoable) | vault, flag for review; **everything** if confidence < 0.8, a check fails, or it's an ID / prescription / lab report | delete, share, upload, send (no such tools exist) |

Without E4B, low-confidence files come straight to you. Inbox items give the reason ("Out of range: HbA1c 7.2 (high)") and offer Approve, Edit (folder, due date) or Leave it.

**Corrections become rules.** Changing a folder, in the inbox or by moving a filed document, stores a rule keyed on type and issuer ("Airtel bills go to Finance/Telecom"). Rejections store skip rules. The next match shows "Applied your rule #N"; the newest rule wins conflicts.

## Why these technical choices

- **E2B by default.** The job is narrow (classify, fill a schema, pick from five tools) and checks catch mistakes, so the smaller model suffices, leaving memory for OCR.
- **The model decides, code executes.** Filenames stay consistent and nothing escapes the library.
- **Schema-constrained JSON at temperature 0.** Ollama's `format` prevents most parse failures; required-key checks catch the rest.
- **Verification in code, not another LLM pass.** String grounding is cheap, deterministic and unit-testable, and targets the worst failure: a confidently wrong amount or date.
- **SQLite for everything.** One transactional file, no daemon.
- **Remove capabilities, don't trust judgement.** A missing tool can't be misused.

## Challenges

- **Ambiguous DD/MM dates.** Text can't settle "03/10", so the agent asks.
- **Similar-looking screenshots.** Hashes rate mostly-white screenshots alike, hence the text-similarity requirement.
- **Watcher feedback.** The agent's own moves triggered events; updating the stored path *before* each move fixed it.
- **Gemma 4's thinking mode.** First calls took ~30 s and leaked reasoning into JSON. `think: false` cut a triage call to under 3 s of generation, and sending the image only when OCR text is thin cut it further. With a nested optional `fields` object, E2B returned it empty; making every field required in a flat schema fixed extraction.
- **Trusting model judgement where code can check.** E2B flagged a TSH of 2.1 (range 0.4 to 4.0) as low and planned no reminder for a bill due in 9 days. Both are now computed in code (flags from the reference range; reminders for any future due date). OCR also read ₹450 as 1450 on a UPI screenshot (a text-first reading limit).

## Results

Setup: Apple M1 Pro, 16 GB RAM, Gemma 4 E2B via Ollama, WiFi off.

- Files processed: 22 synthetic files (26 generated; the partial download is ignored and 3 are held back for live drops)
- Average time per file: 4.9 s end to end; 106 s for the whole backlog
- Handled automatically: 11 (filed, 2 reminders created, 1 exact duplicate linked). Sent to inbox: 11: lab report (always confirmed; HbA1c and Vitamin D out of range), prescription and ID card (always confirmed), ambiguous 01/10 due date, prompt-injection note, password-protected PDF, corrupt PDF, near-duplicate screenshot, WiFi password and OTP (vault / review), and a blurry bill whose extracted values failed grounding
- Escalations: none (E4B not enabled in this run)
- Checks that caught model errors: 3 files: a blurry screenshot whose model-read amount and account number are absent from the OCR text, an ambiguous numeric due date, and the prompt-injection note
- `kill -9` mid-backlog: killed 90 s in; on restart 1 task resumed from its 'triage' checkpoint without repeating earlier stages, 0 half-finished actions needed reconciling, and all 22 files completed
- Outbound non-localhost connections: 0 (only 127.0.0.1 to Ollama)
- Tests: 115 passing unit tests using a scripted fake model, plus live tests against real Gemma 4 (`pytest -m live`).

## What's next

- Benchmark E4B for accuracy against latency.
- Encrypt the database.
- Add Hindi and Telugu OCR.
- Build a mobile version on LiteRT.
- Support real user folders with per-folder opt-in.

---
