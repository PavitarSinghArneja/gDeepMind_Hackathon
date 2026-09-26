"""Record the SnapSort demo: drives the real app in Chrome, shows captions, and logs when each scene starts
so the voice-over can be laid on top afterwards (see build.sh).

Needs: the app running at 127.0.0.1:8765 with the backlog already processed, and audio/<scene>.aiff from narration.json.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
URL = "http://127.0.0.1:8765"
NARRATION = dict(json.load(open(HERE / "narration.json")))


def duration(scene: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(HERE / "audio" / f"{scene}.aiff")], capture_output=True, text=True).stdout
    return float(out)


CAPTION_CSS = """
#demo-caption { position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 99;
  width: min(1100px, calc(100vw - 48px)); background: rgba(20, 28, 24, .9); color: #fff; border-radius: 14px;
  padding: 14px 22px; font: 500 21px/1.45 "Avenir Next", system-ui, sans-serif; text-align: center; }
.toast { bottom: 120px !important; }
.drawer .preview { filter: blur(14px); }
"""


def api_file(name_part: str):
    for f in httpx.get(f"{URL}/api/files", timeout=10).json():
        if name_part.lower() in (f["name"] + (f["title"] or "")).lower():
            return f
    return None


def wait_for(name_part: str, statuses=("done", "needs_human"), timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        f = api_file(name_part)
        if f and f["status"] in statuses:
            return f
        time.sleep(1)
    raise TimeoutError(f"{name_part} not processed in {timeout}s")


def upload(path: Path):
    httpx.post(f"{URL}/api/upload", params={"name": path.name}, content=path.read_bytes(), timeout=30)


def main() -> None:
    video_dir = HERE / "raw"
    video_dir.mkdir(exist_ok=True)
    marks: list[tuple[str, float]] = []
    cuts: list[tuple[float, float]] = []  # stretches spent only waiting for Gemma, removed in build.py
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(video_dir),
                                  record_video_size={"width": 1440, "height": 900})
        t0 = time.time()
        page = ctx.new_page()
        page.goto(URL)
        page.add_style_tag(content=CAPTION_CSS)
        page.evaluate("() => { const d = document.createElement('div'); d.id = 'demo-caption'; document.body.appendChild(d); }")
        page.wait_for_timeout(1500)

        def scene(name: str):
            marks.append((name, time.time() - t0))
            page.evaluate("t => document.getElementById('demo-caption').textContent = t", NARRATION[name])
            return time.time()

        def hold(start: float, name: str, extra: float = 0.8):
            left = duration(name) + extra - (time.time() - start)
            if left > 0:
                page.wait_for_timeout(int(left * 1000))

        # 1. intro
        s = scene("intro")
        page.hover("#pill-model")
        hold(s, "intro")

        # 2. backlog already processed
        s = scene("backlog")
        page.hover("#inbox-count")
        page.wait_for_timeout(2500)
        page.hover(".card")
        hold(s, "backlog")

        # 3. drop the real lab report and watch the pipeline run
        s = scene("drop_medical")
        page.evaluate("() => document.getElementById('dropzone').classList.remove('hidden')")
        page.wait_for_timeout(1400)
        page.evaluate("() => document.getElementById('dropzone').classList.add('hidden')")
        upload(ROOT / "mock" / "MedicalReport.pdf")
        page.evaluate("() => toast('Got MedicalReport.pdf. Watch “What I\\'m doing” as I sort it.', [])")
        hold(s, "drop_medical", extra=2.5)
        wait_from = time.time() - t0
        medical = wait_for("MedicalReport")
        if time.time() - t0 - wait_from > 4:
            cuts.append((wait_from, time.time() - t0 - 2.0))
        page.wait_for_timeout(1200)

        # 4. open its pipeline
        s = scene("pipeline")
        page.evaluate(f"openDrawer({medical['id']})")
        page.wait_for_timeout(1500)
        page.evaluate("() => document.getElementById('drawer').scrollTo({ top: 260, behavior: 'smooth' })")
        page.wait_for_timeout(3000)
        page.evaluate("() => document.getElementById('drawer').scrollTo({ top: 620, behavior: 'smooth' })")
        hold(s, "pipeline")

        # 5. lock it in the vault
        s = scene("vault")
        page.locator(".do-vault").scroll_into_view_if_needed()
        page.wait_for_timeout(1200)
        page.click(".do-vault")
        page.wait_for_timeout(2000)
        page.evaluate("() => document.getElementById('drawer').scrollTo({ top: 0, behavior: 'smooth' })")
        page.wait_for_timeout(1500)
        page.locator(".locked").scroll_into_view_if_needed()
        hold(s, "vault")
        page.evaluate("closeDrawer()")

        # 6. drop the coffee receipt; it shows a card number, so it asks first
        page.evaluate("t => document.getElementById('demo-caption').textContent = t", "Dropping in a coffee receipt…")
        page.evaluate("() => document.getElementById('dropzone').classList.remove('hidden')")
        page.wait_for_timeout(1200)
        page.evaluate("() => document.getElementById('dropzone').classList.add('hidden')")
        upload(ROOT / "mock" / "02_coffee_receipt.png")
        page.wait_for_timeout(2500)
        wait_from = time.time() - t0
        receipt = wait_for("coffee_receipt", timeout=180)
        if time.time() - t0 - wait_from > 4:
            cuts.append((wait_from, time.time() - t0 - 1.5))
        page.wait_for_timeout(1500)
        page.locator(".inbox-item", has_text="Coffee").first.scroll_into_view_if_needed()
        s = scene("drop_receipt")
        page.locator(".inbox-item", has_text="Coffee").first.hover()
        hold(s, "drop_receipt", extra=1.5)

        # 7. one clear button in the inbox, then undo is offered
        s = scene("inbox")
        vault_btn = page.locator(".inbox-item .approve", has_text="Lock in vault").first
        vault_btn.scroll_into_view_if_needed()
        vault_btn.hover()
        page.wait_for_timeout(3500)
        vault_btn.click()
        hold(s, "inbox")

        # 8. ask
        s = scene("ask")
        page.click("#ask-q")
        page.keyboard.type("what's my wifi password?", delay=45)
        page.keyboard.press("Enter")
        page.wait_for_selector(".answer .ans", timeout=90000)
        page.wait_for_timeout(2200)
        if page.locator(".answer .blur").count():
            page.click(".answer .blur")
        hold(s, "ask")

        # 9. export
        s = scene("export")
        page.click(".open-export")
        page.wait_for_timeout(1500)
        page.click('.preset[data-range="year"]')
        hold(s, "export")
        page.click(".close-export")

        # 10. outro
        s = scene("outro")
        page.evaluate("() => { document.getElementById('answer').classList.add('hidden'); window.scrollTo({ top: 0, behavior: 'smooth' }); }")
        hold(s, "outro", extra=1.5)

        video_path = page.video.path()
        ctx.close()
        browser.close()

    (HERE / "marks.json").write_text(json.dumps({"video": str(video_path), "marks": marks, "cuts": cuts}, indent=1))
    print("recorded", video_path)
    for name, at in marks:
        print(f"  {at:6.1f}s  {name}")
    print("  cuts:", [(round(a, 1), round(b, 1)) for a, b in cuts])


if __name__ == "__main__":
    main()
