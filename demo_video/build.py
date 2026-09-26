"""Lay the voice-over on the recorded video at each scene's start and write snapsort_demo.mp4 + snapsort_demo.srt."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEAD = 0.3  # seconds between a caption appearing and the voice starting


def dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True).stdout)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> None:
    info = json.loads((HERE / "marks.json").read_text())
    text = dict(json.load(open(HERE / "narration.json")))
    cuts = sorted(info.get("cuts", []))
    shift = lambda t: t - sum(min(b, t) - a for a, b in cuts if a < t)
    marks = [(name, shift(at)) for name, at in info["marks"]]
    total = dur(Path(info["video"]))
    keep, pos = [], 0.0
    for a, b in cuts:
        keep.append((pos, a)); pos = b
    keep.append((pos, total))
    trims = "".join(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{i}];" for i, (a, b) in enumerate(keep))
    video_filter = trims + "".join(f"[v{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=0[vout]"
    inputs, filters = ["-i", info["video"]], [video_filter]
    for i, (name, at) in enumerate(marks, start=1):
        inputs += ["-i", str(HERE / "audio" / f"{name}.aiff")]
        delay = int((at + LEAD) * 1000)
        filters.append(f"[{i}:a]adelay={delay}|{delay},aresample=44100[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(1, len(marks) + 1))
    filters.append(f"{mix}amix=inputs={len(marks)}:normalize=0[aout]")
    out = HERE / "snapsort_demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
                    "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
                    "-preset", "medium", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)], check=True)

    lines = []
    for n, (name, at) in enumerate(marks, start=1):
        start = at + LEAD
        end = start + dur(HERE / "audio" / f"{name}.aiff")
        lines += [str(n), f"{srt_time(start)} --> {srt_time(end)}", text[name], ""]
    (HERE / "snapsort_demo.srt").write_text("\n".join(lines))
    print(f"wrote {out} ({dur(out):.0f}s) and snapsort_demo.srt")


if __name__ == "__main__":
    main()
