"""The agent loop for one file:
sense → dedupe → triage (E2B, escalate to E4B) → extract (E4B, fall back to E2B) → check → plan → gate
→ act (journaled) → check → index → done, or hand off to a person.

Each stage saves its result in the task checkpoint, so a crash resumes from the last finished stage and
never repeats a model call it already made.
"""
from __future__ import annotations

import difflib
import time
from pathlib import Path

from .. import db, events, human, search, taskq, tools
from ..config import Settings
from ..llm import LLM, LLMError, LLMUnavailable, generate_with_fallback
from ..sense.extract import Content, extract
from ..sense.fingerprint import hamming
from . import planner, prompts, rules, verifier
from .actions import Action
from .policy import Decision, Policy, gate
from .schemas import AMOUNT_KEYS, DATE_KEYS, DOC_TYPES, FIELD_SPECS, TRIAGE_SCHEMA, extract_schema

FILE_COLUMNS = {"doc_type", "sensitivity", "confidence", "title", "summary", "fields_json", "checks_json",
                "text", "status", "dhash"}
NEAR_DUP_BITS = 6
NEAR_DUP_TEXT = 0.8


class NeedsHuman(Exception):
    def __init__(self, reason: str, actions: list[Action] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.actions = actions or []


def _clamp(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _a(doc_type: str) -> str:
    words = doc_type.replace("_", " ")
    return ("an " if words[0] in "aeiou" else "a ") + words


def _clean_triage(out: dict, model: str, filename: str) -> dict:
    return {
        "doc_type": out.get("doc_type") if out.get("doc_type") in DOC_TYPES else "other",
        "sensitivity": out.get("sensitivity") if out.get("sensitivity") in ("low", "medium", "high") else "high",
        "contains_secret": bool(out.get("contains_secret")),
        "title": str(out.get("title") or "").strip()[:80] or filename,
        "confidence": _clamp(out.get("confidence")),
        "model": model,
    }


def _handoff_reason(decision: Decision) -> str:
    parts = list(decision.reasons)
    for a in decision.confirm:
        if a.tool == "vault":
            parts.append("This file shows a password or code. Move it into the encrypted vault?")
        elif a.tool == "flag_for_review" and a.args.get("reason"):
            parts.append(a.args["reason"])
    return ". ".join(p.rstrip(".") for p in parts) or "These actions need your OK"


class Pipeline:
    def __init__(self, conn, settings: Settings, llm: LLM, policy: Policy):
        self.conn, self.settings, self.llm, self.policy = conn, settings, llm, policy
        self._cache: dict[int, Content] = {}

    # ---------- driver ----------
    def run_once(self) -> bool:
        task = taskq.claim(self.conn, self.settings.lease_seconds)
        if task is None:
            return False
        self.process(task)
        return True

    def process(self, task) -> None:
        tid, fid = task["id"], task["file_id"]
        started = time.time()
        try:
            state = self._stages(task)
            taskq.finish(self.conn, tid, state)
            self._set_file(fid, status=state)
            if state == "done":
                secs = round(time.time() - started, 1)
                events.emit(self.conn, "done", f"Done in {secs}s", file_id=fid, data={"seconds": secs})
        except NeedsHuman as h:
            human.open_item(self.conn, task_id=tid, file_id=fid, reason=h.reason, actions=h.actions)
            taskq.finish(self.conn, tid, "needs_human")
            self._set_file(fid, status="needs_human")
            events.emit(self.conn, "handoff", f"Needs you: {h.reason}", file_id=fid, level="warn")
        except LLMUnavailable as e:
            taskq.defer(self.conn, tid, 10, str(e))
            events.emit(self.conn, "model", "The model isn't reachable. Holding this file and retrying in 10s.",
                        file_id=fid, level="warn", data={"error": str(e)})
        except Exception as e:
            state = taskq.fail(self.conn, tid, f"{type(e).__name__}: {e}", self.settings.max_attempts)
            if state == "failed":
                human.open_item(self.conn, task_id=tid, file_id=fid, actions=[],
                                reason=f"I couldn't process this after {self.settings.max_attempts} tries ({e})")
                self._set_file(fid, status="failed")
                events.emit(self.conn, "error", f"Gave up: {e}", file_id=fid, level="error")
            else:
                events.emit(self.conn, "retry", f"Hit a problem ({e}). Retrying shortly.", file_id=fid, level="warn")
        finally:
            self._cache.pop(fid, None)

    # ---------- the loop ----------
    def _stages(self, task) -> str:
        tid, fid = task["id"], task["file_id"]
        cp = taskq.get_checkpoint(task)
        if cp:
            events.emit(self.conn, "recover", f"Resuming from the '{task['stage']}' stage", file_id=fid)
        path = Path(self._file(fid)["current_path"])

        # 1. SENSE
        if "sensed" not in cp:
            if not path.exists():
                events.emit(self.conn, "sense", "The file is gone, so I skipped it", file_id=fid, level="warn")
                return "cancelled"
            content = self._content(fid, path)
            if content.error == "encrypted":
                raise NeedsHuman("This PDF is password-protected. Save an unlocked copy, or leave it as it is.")
            if content.error == "corrupt":
                raise NeedsHuman("This file looks corrupt or incomplete, so I couldn't read it.")
            if content.kind == "unsupported":
                self._set_file(fid, doc_type="other", sensitivity="low", confidence=1.0, title=path.name,
                               summary=f"{path.suffix or 'Unknown'} file. Not a document type I read, so it's indexed by name only.")
                search.index_file(self.conn, self.llm, fid)
                events.emit(self.conn, "sense", "Not a document I can read. Indexed by name only.", file_id=fid)
                return "done"
            self._set_file(fid, text=content.text, dhash=content.dhash)
            note = " after enhancing a blurry image" if content.enhanced else ""
            events.emit(self.conn, "sense", f"Read {content.kind.replace('_', ' ')}: {len(content.text)} characters{note}", file_id=fid)
            cp = taskq.checkpoint(self.conn, tid, "dedupe", sensed=True, kind=content.kind)

        # 2. DUPLICATES
        if "dup" not in cp:
            cp = taskq.checkpoint(self.conn, tid, "triage", dup=self._find_duplicate(fid))
        if cp["dup"]:
            return self._handle_duplicate(tid, fid, cp["dup"])

        # 3. TRIAGE
        if "triage" not in cp:
            tri = self._triage(fid, path)
            cp = taskq.checkpoint(self.conn, tid, "extract", triage=tri)
            self._set_file(fid, doc_type=tri["doc_type"], sensitivity=tri["sensitivity"], confidence=tri["confidence"], title=tri["title"])
            events.emit(self.conn, "triage", f"Looks like {_a(tri['doc_type'])} ({tri['confidence']:.0%} sure, {tri['model']})", file_id=fid, data=tri)
        tri = cp["triage"]
        doc_type = tri["doc_type"]

        # 4. EXTRACT
        if "extraction" not in cp:
            ext = self._extract(fid, path, doc_type)
            cp = taskq.checkpoint(self.conn, tid, "check", extraction=ext)
            shown = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in ext["fields"].items() if isinstance(v, str) and v and k != "secret")
            events.emit(self.conn, "extract", f"Pulled {len(ext['fields'])} fields with {ext['model']}" + (f": {shown[:160]}" if shown else ""), file_id=fid)
        ext = cp["extraction"]
        fields = ext["fields"]

        # 5. CHECK before acting
        if "checks" not in cp:
            text = self._file(fid)["text"]
            checks = verifier.verify_extraction(doc_type, fields, text)
            if len(verifier.norm(text)) < 25:
                checks += self._visual_checks(fid, path, fields)
            cp = taskq.checkpoint(self.conn, tid, "plan", checks=[c.to_dict() for c in checks])
            self._set_file(fid, fields_json=db.dumps(fields), checks_json=db.dumps(cp["checks"]), summary=ext["summary"])
            failed = [c.detail for c in checks if not c.ok]
            events.emit(self.conn, "verify", f"{len(checks) - len(failed)}/{len(checks)} checks passed" + (f". {failed[0]}" if failed else ""),
                        file_id=fid, level="warn" if failed else "info", data={"checks": cp["checks"]})
        failed_checks = [c["detail"] for c in cp["checks"] if not c["ok"]]

        # 6. PLAN
        if "actions" not in cp:
            acts = planner.build_actions(self.llm, self.settings, doc_type=doc_type, triage=tri, extraction=ext, ext=path.suffix.lower())
            acts, hit = rules.apply_rules(self.conn, acts, doc_type, fields)
            for rid in hit:
                events.emit(self.conn, "learn", f"Applied your rule #{rid}", file_id=fid)
            cp = taskq.checkpoint(self.conn, tid, "act", actions=[a.to_dict() for a in acts])
            events.emit(self.conn, "plan", "Plan: " + "; ".join(f"{a.tool.replace('_', ' ')} ({a.why})" for a in acts), file_id=fid)
        actions = [Action.from_dict(a) for a in cp["actions"]]

        # 7. GATE
        decision = gate(self.policy, actions, doc_type=doc_type, confidence=min(tri["confidence"], ext["confidence"]), failed_checks=failed_checks)
        for b in decision.blocked:
            events.emit(self.conn, "policy", f"Blocked “{b.tool}”: not an action I'm allowed to take", file_id=fid, level="warn")

        # 8. ACT (the journal, not the checkpoint, says what already happened)
        done = tools.applied_for_task(self.conn, tid)
        journal_ids = list(done.values())
        for a in decision.auto:
            if a.tool not in done:
                journal_ids.append(tools.execute(self.conn, self.settings, task_id=tid, file_id=fid, action=a))

        # 9. CHECK after acting
        post = tools.postconditions(self.conn, journal_ids)
        bad = [c for c in post if not c.ok]
        if bad:
            undone = tools.undo_task(self.conn, self.settings, tid)
            raise NeedsHuman(f"A change didn't land as expected ({bad[0].detail}), so I rolled back {undone} change(s)",
                             decision.auto + decision.confirm)
        if post:
            events.emit(self.conn, "verify", f"{len(post)}/{len(post)} changes confirmed on disk", file_id=fid)

        # 10. INDEX, then hand off anything that needs a person
        taskq.checkpoint(self.conn, tid, "index")
        search.index_file(self.conn, self.llm, fid)
        if decision.confirm or decision.blocked:
            raise NeedsHuman(_handoff_reason(decision), decision.confirm)
        return "done"

    # ---------- stage helpers ----------
    def _handle_duplicate(self, tid: int, fid: int, dup: dict) -> str:
        original = self._file(dup["of"])
        self._set_file(fid, doc_type=original["doc_type"], sensitivity=original["sensitivity"], title=original["title"],
                       summary=original["summary"], fields_json=original["fields_json"])
        action = Action("mark_duplicate", {"of_file_id": dup["of"]},
                        f"same content as #{dup['of']}" if dup["exact"] else f"looks almost identical to #{dup['of']}")
        if not dup["exact"]:
            raise NeedsHuman(f"This looks like a near-copy of “{original['title']}”. Mark it as a duplicate?", [action])
        if "mark_duplicate" not in tools.applied_for_task(self.conn, tid):
            tools.execute(self.conn, self.settings, task_id=tid, file_id=fid, action=action)
        search.index_file(self.conn, self.llm, fid)
        return "done"

    def _find_duplicate(self, fid: int) -> dict | None:
        f = self._file(fid)
        same = self.conn.execute(
            "SELECT id FROM files WHERE sha256=? AND id<>? AND status IN ('done', 'needs_human') ORDER BY id LIMIT 1",
            (f["sha256"], fid)).fetchone()
        if same:
            return {"of": same["id"], "exact": True}
        if not f["dhash"]:
            return None
        for r in self.conn.execute(
                "SELECT id, dhash, text FROM files WHERE dhash IS NOT NULL AND id<>? AND status IN ('done', 'needs_human')", (fid,)):
            if (hamming(f["dhash"], r["dhash"]) <= NEAR_DUP_BITS
                    and difflib.SequenceMatcher(None, f["text"][:2000], r["text"][:2000]).ratio() >= NEAR_DUP_TEXT):
                return {"of": r["id"], "exact": False}
        return None

    def _fallback_event(self, fid: int):
        return lambda model, err: events.emit(self.conn, "model", f"{model} failed ({err}), falling back", file_id=fid, level="warn")

    def _triage(self, fid: int, path: Path) -> dict:
        content = self._content(fid, path)
        prompt = prompts.triage_prompt(path.name, content.text)
        out, model = generate_with_fallback(self.llm, [self.settings.triage_model, self.settings.work_model], prompt,
                                            TRIAGE_SCHEMA, content.images[:1], on_fallback=self._fallback_event(fid))
        best = _clean_triage(out, model, path.name)
        if best["confidence"] < self.policy.confidence_threshold and model != self.settings.work_model:
            events.emit(self.conn, "triage", f"Only {best['confidence']:.0%} sure it's {_a(best['doc_type'])}, so I'm asking {self.settings.work_model}", file_id=fid)
            try:
                second = _clean_triage(self.llm.generate_json(self.settings.work_model, prompt, TRIAGE_SCHEMA, content.images[:1]),
                                       self.settings.work_model, path.name)
                if second["confidence"] >= best["confidence"]:
                    best = second
            except LLMUnavailable:
                raise
            except LLMError:
                pass
        return best

    def _extract(self, fid: int, path: Path, doc_type: str) -> dict:
        content = self._content(fid, path)
        out, model = generate_with_fallback(self.llm, [self.settings.work_model, self.settings.triage_model],
                                            prompts.extract_prompt(doc_type, path.name, content.text), extract_schema(doc_type),
                                            content.images[:1], on_fallback=self._fallback_event(fid))
        raw = out.get("fields") if isinstance(out.get("fields"), dict) else {}
        return {"summary": str(out.get("summary") or "")[:300],
                "fields": {k: v for k, v in raw.items() if k in FIELD_SPECS[doc_type]},
                "confidence": _clamp(out.get("confidence")), "model": model}

    def _visual_checks(self, fid: int, path: Path, fields: dict) -> list[verifier.Check]:
        content = self._content(fid, path)
        if not content.images:
            return []
        for key in (*AMOUNT_KEYS, *DATE_KEYS):
            value = fields.get(key)
            if isinstance(value, str) and value:
                return [verifier.confirm_in_image(self.llm, self.settings.work_model, content.images[0], key, value)]
        return []

    # ---------- small helpers ----------
    def _content(self, fid: int, path: Path) -> Content:
        if fid not in self._cache:
            self._cache[fid] = extract(path)
        return self._cache[fid]

    def _file(self, fid: int):
        return self.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()

    def _set_file(self, fid: int, **cols) -> None:
        unknown = set(cols) - FILE_COLUMNS
        if unknown:
            raise ValueError(f"not a file column: {unknown}")
        sets = ", ".join(f"{k}=?" for k in cols)
        self.conn.execute(f"UPDATE files SET {sets}, updated_at=? WHERE id=?", (*cols.values(), db.now(), fid))
