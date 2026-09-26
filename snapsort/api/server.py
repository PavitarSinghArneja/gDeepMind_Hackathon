"""Local web API and UI host. Binds to 127.0.0.1 only; nothing here talks to the internet."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import psutil
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import db, events, human, search, taskq, tools, views
from ..agent.policy import Policy
from ..agent.schemas import CATEGORY_FOLDERS
from ..config import Settings
from ..llm import LLM, LLMError, LLMUnavailable, installed
from ..sense.watcher import initial_scan

WEB = Path(__file__).resolve().parents[2] / "web"
MEDIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif", ".txt": "text/plain; charset=utf-8",
         ".md": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8"}
EXTRA_FOLDERS = {"Finance/Telecom", "Finance/Utilities", "Finance/Tax", "Health/Records", "Home"}  # choices for corrections


class ApproveBody(BaseModel):
    folder: str | None = None
    due_date: str | None = None


class RejectBody(BaseModel):
    learn: bool = True


class RefileBody(BaseModel):
    folder: str


class AskBody(BaseModel):
    question: str


def network_status() -> dict:
    """Is there a default route (internet), and does this process hold any non-localhost connection?"""
    internet = None
    cmd = ["route", "-n", "get", "default"] if sys.platform == "darwin" else ["ip", "route", "show", "default"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        internet = r.returncode == 0 and bool(r.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    proc = psutil.Process()
    conns = proc.net_connections(kind="inet") if hasattr(proc, "net_connections") else proc.connections(kind="inet")
    outbound = sorted({f"{c.raddr.ip}:{c.raddr.port}" for c in conns if c.raddr and c.raddr.ip not in ("127.0.0.1", "::1")})
    return {"internet": internet, "outbound": outbound}


def create_app(settings: Settings, llm: LLM, policy: Policy) -> FastAPI:
    app = FastAPI(title="SnapSort")
    root = settings.root

    def get_conn():
        c = db.connect(settings.db_path)
        try:
            yield c
        finally:
            c.close()

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/api/state")
    def state(c=Depends(get_conn)):
        try:
            names = llm.models()
            available = all(installed(names, m) for m in (settings.triage_model, settings.work_model))
        except LLMError:
            names, available = [], False
        learned = {db.loads(r["effect_json"], {}).get("folder") for r in c.execute("SELECT effect_json FROM rules")}
        return {
            "tasks": taskq.counts(c),
            "models": {"triage": settings.triage_model, "work": settings.work_model, "embed": settings.embed_model,
                       "available": available, "installed": names},
            "network": network_status(),
            "stats": views.stats(c),
            "folders": sorted((set(CATEGORY_FOLDERS.values()) | EXTRA_FOLDERS | learned) - {None}),
            "policy": {"auto": sorted(policy.auto), "confirm": sorted(policy.confirm), "never": list(policy.never),
                       "always_confirm_doc_types": sorted(policy.always_confirm_doc_types),
                       "confidence_threshold": policy.confidence_threshold},
        }

    @app.get("/api/events/recent")
    def recent_events(limit: int = 150, c=Depends(get_conn)):
        last = c.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        return events.since(c, max(0, last - limit), limit)

    @app.get("/api/events")
    async def stream_events(request: Request, after: int = 0):
        start = int(request.headers.get("last-event-id") or after)

        async def gen():
            c = db.connect(settings.db_path)
            last, idle = start, 0
            try:
                while not await request.is_disconnected():
                    rows = events.since(c, last)
                    for r in rows:
                        last = r["id"]
                        yield f"id: {r['id']}\ndata: {json.dumps(r)}\n\n"
                    idle = 0 if rows else idle + 1
                    if idle and idle % 30 == 0:
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.5)
            finally:
                c.close()

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/files")
    def list_files(doc_type: str | None = None, c=Depends(get_conn)):
        return views.list_files(c, root, doc_type)

    @app.get("/api/files/{fid}")
    def file_detail(fid: int, c=Depends(get_conn)):
        d = views.file_detail(c, root, fid)
        if d is None:
            raise HTTPException(404, "no such file")
        return d

    @app.post("/api/files/{fid}/reveal")
    def reveal(fid: int, c=Depends(get_conn)):
        d = views.file_detail(c, root, fid, reveal=True)
        if d is None:
            raise HTTPException(404, "no such file")
        events.emit(c, "human", "You revealed a protected value", file_id=fid)
        return d

    @app.get("/api/files/{fid}/preview")
    def preview(fid: int, reveal: bool = False, c=Depends(get_conn)):
        row = c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such file")
        path = Path(row["current_path"])
        if not path.exists():
            raise HTTPException(404, "the file isn't on disk")
        if row["vaulted"]:
            if not reveal:
                raise HTTPException(403, "This file is encrypted in the vault. Reveal it to view.")
            events.emit(c, "human", "You opened a vaulted file", file_id=fid)
            data = Fernet(tools.vault_key(settings)).decrypt(path.read_bytes())
            return Response(data, media_type=MEDIA.get(Path(row["original_path"]).suffix.lower(), "application/octet-stream"))
        return FileResponse(path, media_type=MEDIA.get(path.suffix.lower(), "application/octet-stream"))

    @app.post("/api/files/{fid}/refile")
    def refile(fid: int, body: RefileBody, c=Depends(get_conn)):
        try:
            human.refile(c, settings, fid, body.folder)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except tools.ToolError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.get("/api/inbox")
    def inbox(c=Depends(get_conn)):
        return human.list_open(c)

    @app.post("/api/inbox/{iid}/approve")
    def approve(iid: int, body: ApproveBody, c=Depends(get_conn)):
        try:
            return {"journal_ids": human.approve(c, settings, iid, body.model_dump(exclude_none=True))}
        except KeyError as e:
            raise HTTPException(404, str(e))
        except (ValueError, tools.ToolError) as e:
            raise HTTPException(409, str(e))

    @app.post("/api/inbox/{iid}/reject")
    def reject(iid: int, body: RejectBody, c=Depends(get_conn)):
        try:
            human.reject(c, iid, body.learn)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.get("/api/reminders")
    def reminders(c=Depends(get_conn)):
        return views.reminders(c)

    @app.post("/api/reminders/{rid}/done")
    def reminder_done(rid: int, c=Depends(get_conn)):
        c.execute("UPDATE reminders SET state='done' WHERE id=?", (rid,))
        return {"ok": True}

    @app.get("/api/rules")
    def list_rules(c=Depends(get_conn)):
        return views.rules_list(c)

    @app.delete("/api/rules/{rid}")
    def forget_rule(rid: int, c=Depends(get_conn)):
        c.execute("DELETE FROM rules WHERE id=?", (rid,))
        events.emit(c, "learn", f"Forgot rule #{rid}")
        return {"ok": True}

    @app.post("/api/journal/{jid}/undo")
    def undo(jid: int, c=Depends(get_conn)):
        try:
            tools.undo(c, settings, jid)
        except tools.ToolError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/ask")
    def ask(body: AskBody, c=Depends(get_conn)):
        try:
            return search.ask(c, llm, settings, body.question)
        except LLMUnavailable:
            rows = search.search(c, llm, body.question)
            return {"found": bool(rows), "answer": "The model isn't running, so here are keyword matches.",
                    "files": [views.public_file(r, root) for r in rows], "grounded": True, "sensitive": False}

    @app.get("/api/expenses")
    def expenses(start: str | None = None, end: str | None = None, format: str = "json", c=Depends(get_conn)):
        rows = views.expenses(c, root, start, end)
        if format != "csv":
            return {"rows": rows, "total": round(sum(r["amount"] for r in rows), 2)}
        import csv, io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "Paid to", "Amount (INR)", "Type", "Verified against document", "File"])
        for r in rows:
            w.writerow([r["date"], r["paid_to"], f"{r['amount']:.2f}", r["type"], "yes" if r["verified"] else "no, check it", r["file"]])
        w.writerow(["", "Total", f"{sum(r['amount'] for r in rows):.2f}", "", "", ""])
        name = f"expenses_{start or 'all'}_to_{end or 'now'}.csv"
        events.emit(c, "human", f"You exported {len(rows)} expense(s) to {name}")
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/rescan")
    def rescan(c=Depends(get_conn)):
        return {"new": initial_scan(c, settings)}

    return app
