"""Run SnapSort.

    python -m snapsort              watch folders, process files, serve the UI at http://127.0.0.1:8765
    python -m snapsort --headless   process what's in the folders, print a report, exit
"""
from __future__ import annotations

import argparse
import logging
import threading

from . import db, events, human, taskq, tools, views
from .agent.pipeline import Pipeline
from .agent.policy import load_policy
from .config import Settings
from .llm import OllamaLLM
from .sense.watcher import initial_scan, start_watcher

log = logging.getLogger("snapsort")


def boot(settings: Settings):
    settings.ensure_dirs()
    conn = db.connect(settings.db_path)
    tasks = taskq.recover(conn)
    actions = tools.reconcile(conn, settings)
    if tasks or actions:
        events.emit(conn, "recover", f"Restarted after an interruption: resuming {tasks} task(s), "
                                     f"reconciled {actions} half-finished action(s)")
    new = initial_scan(conn, settings)
    events.emit(conn, "sense", f"Watching {len(settings.watch_dirs)} folders. Found {new} new file(s).")
    return conn


def worker(pipeline: Pipeline, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            busy = pipeline.run_once()
        except Exception:
            log.exception("worker loop error")
            busy = False
        if not busy:
            stop.wait(0.5)


def print_report(conn, settings: Settings) -> None:
    print(f"\n{'file':44} {'type':16} {'status':12} location")
    for f in views.list_files(conn, settings.root):
        print(f"{f['name'][:44]:44} {(f['doc_type'] or '-'):16} {f['status']:12} {f['location']}")
    print("\nNeeds you:")
    for item in human.list_open(conn):
        print(f"  - {item['name']}: {item['reason']}")
    print("\nStats:", views.stats(conn))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="snapsort")
    ap.add_argument("--headless", action="store_true", help="process the folders, print a report and exit")
    ap.add_argument("--no-watch", action="store_true", help="don't watch the folders for new files")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = Settings()
    conn = boot(settings)
    llm = OllamaLLM(settings.ollama_url, settings.embed_model, settings.llm_timeout_s)
    policy = load_policy(settings.policy_path)
    pipeline = Pipeline(conn, settings, llm, policy)

    if args.headless:
        while pipeline.run_once():
            pass
        print_report(conn, settings)
        return

    import uvicorn

    from .api.server import create_app

    stop = threading.Event()
    threading.Thread(target=worker, args=(pipeline, stop), daemon=True, name="worker").start()
    observer = None if args.no_watch else start_watcher(settings)
    print(f"SnapSort is running at http://127.0.0.1:{settings.port}  (Ctrl+C to stop)")
    try:
        uvicorn.run(create_app(settings, llm, policy), host="127.0.0.1", port=settings.port, log_level="warning")
    finally:
        stop.set()
        if observer:
            observer.stop()
            observer.join(timeout=2)


if __name__ == "__main__":
    main()
