"""Record the SnapSort pitch at 1920x1080 by driving the real app in Chrome and capturing frames via the
DevTools screencast. Logs scene start times and waiting stretches to cut; build_pitch.py assembles the video.

Needs: SnapSort running at 127.0.0.1:8765 with the mock backlog processed, mock/MedicalReport.pdf present,
and audio_pitch/<scene>.aiff generated from pitch.json. This really kills and restarts SnapSort mid-recording.
"""
from __future__ import annotations

import base64
import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
URL = "http://127.0.0.1:8765"
TEXT = dict(json.load(open(HERE / "pitch.json")))
FRAMES = HERE / "frames"
PY = str(ROOT / ".venv" / "bin" / "python")

OVERLAY_CSS = """
#demo-caption { position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%); z-index: 100;
  width: min(1320px, calc(100vw - 64px)); background: rgba(20, 28, 24, .92); color: #fff; border-radius: 16px;
  padding: 16px 28px; font: 500 25px/1.45 "Avenir Next", system-ui, sans-serif; text-align: center; }
#demo-caption:empty { display: none; }
#demo-chip { position: fixed; top: 18px; left: 50%; transform: translateX(-50%); z-index: 100; background: #2f5d50; color: #fff;
  border-radius: 999px; padding: 9px 22px; font: 600 20px/1.2 "Avenir Next", system-ui, sans-serif; box-shadow: 0 6px 20px rgba(0,0,0,.18); }
#demo-chip:empty { display: none; }
#demo-slide { position: fixed; inset: 0; z-index: 98; background: #eef1ec; display: flex; flex-direction: column;
  justify-content: center; padding: 0 160px 120px; color: #1f2a24; font-family: "Avenir Next", system-ui, sans-serif;
  opacity: 0; pointer-events: none; transition: opacity .5s; }
#demo-slide.on { opacity: 1; }
#demo-slide .eyebrow { font-size: 24px; color: #5f6d66; margin: 0 0 18px; }
#demo-slide h1 { font: 600 76px/1.08 Charter, Georgia, serif; margin: 0 0 26px; letter-spacing: -.01em; max-width: 20ch; }
#demo-slide p.lede { font-size: 32px; line-height: 1.4; margin: 0; color: #33423a; max-width: 44ch; }
#demo-slide ol.bar { list-style: none; counter-reset: b; padding: 0; margin: 10px 0 0; }
#demo-slide ol.bar li { counter-increment: b; font-size: 38px; line-height: 1.3; margin: 0 0 26px; padding-left: 78px; position: relative; }
#demo-slide ol.bar li::before { content: counter(b); position: absolute; left: 0; top: -2px; width: 52px; height: 52px; border-radius: 50%;
  background: #2f5d50; color: #fff; display: grid; place-items: center; font: 600 26px/1 "Avenir Next", sans-serif; }
#demo-slide .cols { display: grid; grid-template-columns: repeat(4, 1fr); gap: 22px; margin-top: 16px; }
#demo-slide .col { background: #fbfcfa; border-radius: 18px; padding: 26px 24px; border-top: 8px solid #2f5d50; }
#demo-slide .col.ask { border-top-color: #e9cf7f; } #demo-slide .col.wait { border-top-color: #c79a1c; } #demo-slide .col.never { border-top-color: #b5462e; }
#demo-slide .col h3 { font: 600 30px/1.2 Charter, Georgia, serif; margin: 0 0 12px; }
#demo-slide .col p { font-size: 24px; line-height: 1.4; margin: 0; color: #33423a; }
#demo-slide .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 22px; margin-top: 10px; }
#demo-slide .stat { background: #fbfcfa; border-radius: 18px; padding: 26px 26px; }
#demo-slide .stat b { display: block; font: 600 54px/1.1 Charter, Georgia, serif; color: #2f5d50; margin-bottom: 8px; }
#demo-slide .stat span { font-size: 23px; line-height: 1.35; color: #33423a; }
#demo-term { position: fixed; right: 40px; top: 90px; z-index: 99; width: 760px; background: #1c2420; color: #d9e8df; border-radius: 14px;
  padding: 18px 22px; font: 21px/1.5 Menlo, monospace; box-shadow: 0 14px 40px rgba(0,0,0,.35); }
#demo-term:empty { display: none; }
#demo-term .p { color: #e9cf7f; }
.demo-hl { outline: 5px solid #e9cf7f !important; outline-offset: 4px; border-radius: 10px; }
.toast { bottom: 150px !important; }
.drawer .preview { filter: blur(16px); }
"""

OVERLAY_JS = """
() => {
  for (const id of ['demo-caption', 'demo-chip', 'demo-slide', 'demo-term']) {
    if (!document.getElementById(id)) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  }
  window.demoHL = (el, ms) => { el.classList.add('demo-hl'); setTimeout(() => el.classList.remove('demo-hl'), ms); };
}
"""

SLIDES = {
    "hook": """<p class="eyebrow">Google DeepMind x GDG Hyderabad hackathon, Problem Statement 5</p>
      <h1>Your most private files are sitting in Downloads.</h1>
      <p class="lede">Bank statements, lab reports, ID scans, passwords. A chatbot still makes you ask about every file.
      <b>SnapSort is an agent</b> that sorts, checks and protects them on its own, fully offline on Gemma 4.</p>""",
    "bar": """<p class="eyebrow">The bar for a local-first agent</p>
      <ol class="bar"><li>A sense, decide, act, check loop, not a straight arrow</li><li>Clear boundaries for human handoff</li>
      <li>Local state management</li><li>Offline error recovery</li></ol>""",
    "handoff": """<p class="eyebrow">config/policy.yaml</p><h1 style="font-size:60px">Who decides what</h1>
      <div class="cols">
        <div class="col"><h3>Runs on its own</h3><p>File and rename, remind, mark copies. Every one can be undone.</p></div>
        <div class="col ask"><h3>Asks first</h3><p>Lock in the vault, flag for review.</p></div>
        <div class="col wait"><h3>Always waits for you</h3><p>Medical and ID documents, failed checks, anything under 80% confidence.</p></div>
        <div class="col never"><h3>Impossible</h3><p>Delete, share, upload, send. No such tools exist.</p></div>
      </div>""",
    "results": """<p class="eyebrow">Measured on a 16 GB M1 Pro laptop</p><h1 style="font-size:60px">Results</h1>
      <div class="stats">
        <div class="stat"><b>22 files</b><span>sorted in 106 s on Gemma 4 E2B, 11 on its own, 11 handed to me</span></div>
        <div class="stat"><b>0</b><span>outside connections. Only localhost to Ollama</span></div>
        <div class="stat"><b>kill -9</b><span>mid-work: resumed from checkpoints, nothing lost</span></div>
        <div class="stat"><b>3</b><span>checks that stopped a wrong or unsafe action</span></div>
        <div class="stat"><b>9 of 10</b><span>questions find the right file first with EmbeddingGemma</span></div>
        <div class="stat"><b>120</b><span>automated tests, plus live tests on real Gemma 4</span></div>
      </div>""",
    "outro": """<p class="eyebrow">github.com/PavitarSinghArneja/gDeepMind_Hackathon</p>
      <h1>SnapSort</h1>
      <p class="lede">Senses, decides, acts and checks, fully offline on Gemma 4. It keeps its own state, recovers from failure,
      and knows exactly when to ask.</p>""",
}
CHIPS = {1: "The bar 1/4: A loop, not a straight arrow", 2: "The bar 2/4: Clear boundaries for human handoff",
         3: "The bar 3/4: Local state management", 4: "The bar 4/4: Offline error recovery"}


def duration(scene: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(HERE / "audio_pitch" / f"{scene}.aiff")], capture_output=True, text=True).stdout
    return float(out)


def status_of(pattern: str):
    conn = sqlite3.connect(ROOT / "data" / "snapsort.db", timeout=30)
    row = conn.execute("SELECT id, status FROM files WHERE original_path LIKE ? ORDER BY id DESC LIMIT 1", (f"%{pattern}%",)).fetchone()
    conn.close()
    return row


def wait_status(pattern: str, timeout: float = 240) -> int:
    end = time.time() + timeout
    while time.time() < end:
        row = status_of(pattern)
        if row and row[1] in ("done", "needs_human"):
            return row[0]
        time.sleep(1)
    raise TimeoutError(pattern)


def upload(path: Path) -> None:
    httpx.post(f"{URL}/api/upload", params={"name": path.name}, content=path.read_bytes(), timeout=30)


def kill_server() -> None:
    subprocess.run(["pkill", "-9", "-if", "m snapsort"])


def start_server() -> None:
    subprocess.Popen([PY, "-m", "snapsort"], cwd=ROOT, stdout=open("/tmp/snapsort_ui.log", "a"),
                     stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(60):
        try:
            httpx.get(f"{URL}/api/state", timeout=2)
            return
        except httpx.HTTPError:
            time.sleep(0.5)
    raise RuntimeError("SnapSort didn't come back")


def main() -> None:
    shutil.rmtree(FRAMES, ignore_errors=True)
    FRAMES.mkdir()
    frames: list[tuple[float, str]] = []
    marks: list[tuple[str, float]] = []
    cuts: list[tuple[float, float]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)

        def on_frame(e):
            name = f"{len(frames):06d}.jpg"
            (FRAMES / name).write_bytes(base64.b64decode(e["data"]))
            frames.append((e["metadata"]["timestamp"], name))
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": e["sessionId"]})
            except Exception:
                pass

        cdp.on("Page.screencastFrame", on_frame)

        def install():
            page.add_style_tag(content=OVERLAY_CSS)
            page.evaluate(OVERLAY_JS)

        def screencast():
            try:
                cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080})
            except Exception as e:  # it survives a reload, which is fine
                if "already active" not in str(e):
                    raise

        def set_html(el_id: str, html: str):
            page.evaluate("([id, h]) => { document.getElementById(id).innerHTML = h; }", [el_id, html])

        def slide(key: str | None):
            if key:
                set_html("demo-slide", SLIDES[key])
                page.evaluate("() => document.getElementById('demo-slide').classList.add('on')")
            else:
                page.evaluate("() => document.getElementById('demo-slide').classList.remove('on')")
            page.wait_for_timeout(550)

        def chip(n: int | None):
            set_html("demo-chip", CHIPS[n] if n else "")

        def term(lines: list[str] | None):
            html = "<br>".join(f'<span class="p">$</span> {l}' if not l.startswith("#") else l[1:] for l in lines) if lines else ""
            set_html("demo-term", html)

        def hl(locator, ms: int = 3500):
            locator.scroll_into_view_if_needed()
            locator.evaluate("(el, ms) => demoHL(el, ms)", ms)

        def scene(name: str) -> float:
            marks.append((name, time.time()))
            page.evaluate("t => { document.getElementById('demo-caption').textContent = t; }", TEXT[name])
            return time.time()

        def hold(start: float, name: str, extra: float = 0.7):
            left = duration(name) + extra - (time.time() - start)
            if left > 0:
                page.wait_for_timeout(int(left * 1000))

        def cut_from(t_start: float, trim_end: float = 1.2):
            if time.time() - t_start > 3:
                cuts.append((t_start, time.time() - trim_end))

        def drop(paths: list[Path], label: str):
            page.evaluate("() => document.getElementById('dropzone').classList.remove('hidden')")
            page.wait_for_timeout(1300)
            page.evaluate("() => document.getElementById('dropzone').classList.add('hidden')")
            for path in paths:
                upload(path)
            page.evaluate("m => toast(m, [])", label)

        page.goto(URL)
        page.wait_for_timeout(1500)
        install()
        slide("hook")
        screencast()
        t0 = time.time()
        page.wait_for_timeout(600)

        # ---- the problem ----
        s = scene("hook")
        hold(s, "hook")

        # ---- the product ----
        slide(None)
        s = scene("overview")
        page.wait_for_timeout(1500)
        hl(page.locator("#pill-model"), 3000); page.wait_for_timeout(3600)
        hl(page.locator("#pill-net"), 3000); page.wait_for_timeout(3800)
        hl(page.locator("#summary"), 3500); page.wait_for_timeout(2500)
        hl(page.locator(".col-inbox"), 3000)
        hold(s, "overview")

        # ---- the bar ----
        slide("bar")
        s = scene("bar")
        hold(s, "bar")

        # ---- 1. loop ----
        slide(None)
        chip(1)
        s = scene("loop_drop")
        drop([ROOT / "mock" / "MedicalReport.pdf"], "Got MedicalReport.pdf. Watch What I'm doing as I sort it.")
        hl(page.locator(".col-feed"), 4000)
        hold(s, "loop_drop", extra=2.5)
        w = time.time()
        medical = wait_status("MedicalReport")
        cut_from(w)
        page.wait_for_timeout(800)

        s = scene("loop_pipeline")
        page.evaluate(f"openDrawer({medical})")
        page.wait_for_timeout(1800)
        for top in (230, 470, 700):
            page.evaluate(f"() => document.getElementById('drawer').scrollTo({{ top: {top}, behavior: 'smooth' }})")
            page.wait_for_timeout(4200)
        hold(s, "loop_pipeline")
        page.evaluate("closeDrawer()")

        s = scene("loop_checks")
        hl(page.locator(".inbox-item", has_text="not sure I read this right").first, 6000)
        page.wait_for_timeout(6500)
        hl(page.locator(".inbox-item", has_text="tries to give me orders").first, 6000)
        hold(s, "loop_checks")

        # ---- 2. handoff ----
        chip(2)
        slide("handoff")
        s = scene("handoff")
        hold(s, "handoff")
        slide(None)

        s = scene("handoff_demo")
        item = page.locator(f'.inbox-item:has(.file-name[data-id="{medical}"])').first
        hl(item, 3000)
        page.wait_for_timeout(2600)
        item.locator(".file-name").click()
        page.wait_for_timeout(1500)
        page.locator(".do-vault").scroll_into_view_if_needed()
        hl(page.locator(".do-vault"), 2000)
        page.wait_for_timeout(1500)
        page.click(".do-vault")
        page.wait_for_timeout(1800)
        page.evaluate("() => document.getElementById('drawer').scrollTo({ top: 0, behavior: 'smooth' })")
        hold(s, "handoff_demo")
        page.evaluate("closeDrawer()")

        # ---- 3. local state ----
        chip(3)
        s = scene("state")
        airtel = status_of("Airtel_Bill_Sep")
        page.evaluate(f"openDrawer({airtel[0]})")
        page.wait_for_timeout(2500)
        page.locator(".refile").scroll_into_view_if_needed()
        page.wait_for_timeout(3500)
        page.select_option(".refile", "Finance/Telecom")
        page.wait_for_timeout(1400)
        page.click(".do-refile")
        page.wait_for_timeout(1500)
        hl(page.locator("#rules"), 5000)
        hold(s, "state")

        # ---- 4. recovery ----
        chip(4)
        drops = HERE.parent / "mock" / "_demo_drops"
        s = scene("recovery")
        drop([next(drops.glob("Screenshot*.png")), next(drops.glob("Airtel_Bill_*.pdf")), drops / "Apollo_followup.pdf"],
             "Got 3 files. Watch What I'm doing as I sort them.")
        hl(page.locator(".col-feed"), 4000)
        w = time.time()
        wait_status("at 10.05.31", timeout=120)  # first file finished, second one in progress
        page.wait_for_timeout(2500)
        cut_from(w, trim_end=3.5)
        term(["kill -9 $(pgrep -f 'python -m snapsort')"])
        page.wait_for_timeout(900)
        kill_server()
        term(["kill -9 $(pgrep -f 'python -m snapsort')", "#[1]  killed     python -m snapsort"])
        hold(s, "recovery", extra=1.5)

        term(["kill -9 $(pgrep -f 'python -m snapsort')", "#[1]  killed     python -m snapsort", "python -m snapsort"])
        start_server()
        term(["kill -9 $(pgrep -f 'python -m snapsort')", "#[1]  killed     python -m snapsort", "python -m snapsort",
              "#SnapSort is running at http://127.0.0.1:8765"])
        page.wait_for_timeout(1800)
        page.reload()
        install()
        chip(4)
        screencast()
        page.wait_for_timeout(1500)
        w = time.time()
        wait_status("Airtel_Bill_Oct", timeout=240)
        wait_status("Apollo_followup", timeout=240)
        page.wait_for_timeout(1500)
        cut_from(w)

        s = scene("recovery_after")
        restarted = page.locator("#feed .ev", has_text="Restarted after an interruption").first
        restarted.scroll_into_view_if_needed()
        hl(restarted, 5000)
        page.wait_for_timeout(3500)
        resumed = page.locator("#feed .ev", has_text="Resuming from").first
        if resumed.count():
            hl(resumed, 4000)
        page.wait_for_timeout(4000)
        applied = page.locator("#feed .ev", has_text="Applied your rule").first
        if applied.count():
            hl(applied, 4000)
        page.fill("#lib-search", "airtel")
        page.wait_for_timeout(800)
        hl(page.locator(".col-library"), 4000)
        hold(s, "recovery_after")
        page.fill("#lib-search", "")

        # ---- everyday use ----
        chip(None)
        s = scene("ask")
        page.click("#ask-q")
        page.keyboard.type("what's my wifi password?", delay=40)
        page.keyboard.press("Enter")
        page.wait_for_selector(".answer .ans", timeout=120000)
        page.wait_for_timeout(2500)
        if page.locator(".answer .blur").count():
            page.click(".answer .blur")
        hold(s, "ask")

        s = scene("export")
        page.click(".open-export")
        page.wait_for_timeout(1200)
        page.click('.preset[data-range="year"]')
        page.wait_for_timeout(2200)
        hl(page.locator("#exp-download"), 2500)
        page.wait_for_timeout(2600)
        page.click(".close-export")
        mark_paid = page.locator(".done-reminder").first
        hl(mark_paid, 1500)
        page.wait_for_timeout(1000)
        mark_paid.click()
        page.wait_for_timeout(1400)
        if page.locator(".toast-undo").count():
            page.click(".toast-undo")
        elif page.locator(".undo-reminder").count():
            page.click(".undo-reminder")
        hold(s, "export")

        # ---- close ----
        slide("results")
        s = scene("results")
        hold(s, "results")
        slide("outro")
        s = scene("outro")
        hold(s, "outro", extra=2.0)
        cdp.send("Page.stopScreencast")
        end = time.time()
        browser.close()

    (HERE / "pitch_marks.json").write_text(json.dumps({"t0": t0, "end": end, "frames": frames, "marks": marks, "cuts": cuts}))
    print(f"{len(frames)} frames, {end - t0:.0f}s raw")
    for name, at in marks:
        print(f"  {at - t0:6.1f}s  {name}")
    print("  cuts:", [(round(a - t0, 1), round(b - t0, 1)) for a, b in cuts])


if __name__ == "__main__":
    main()
