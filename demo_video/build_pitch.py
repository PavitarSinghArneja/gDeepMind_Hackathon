"""Assemble the pitch: drop the waiting stretches, keep real frame timing, lay the voice-over at each scene start,
and write snapsort_pitch.mp4 (1920x1080, 30 fps) plus snapsort_pitch.srt."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEAD = 0.35


def dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True).stdout)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> None:
    info = json.loads((HERE / "pitch_marks.json").read_text())
    text = dict(json.load(open(HERE / "pitch.json")))
    t0, end, cuts = info["t0"], info["end"], sorted(info["cuts"])

    def shift(t: float) -> float:
        return (t - t0) - sum(min(b, t) - a for a, b in cuts if a < t)

    kept = [(shift(ts), name) for ts, name in info["frames"]
            if ts >= t0 and not any(a <= ts < b for a, b in cuts)]
    kept.sort()
    total = shift(end)
    lines = []
    for i, (t, name) in enumerate(kept):
        nxt = kept[i + 1][0] if i + 1 < len(kept) else total
        lines += [f"file 'frames/{name}'", f"duration {max(nxt - t, 0.001):.4f}"]
    lines.append(f"file 'frames/{kept[-1][1]}'")
    (HERE / "frames.txt").write_text("\n".join(lines) + "\n")

    marks = [(name, shift(at)) for name, at in info["marks"]]
    inputs = ["-f", "concat", "-safe", "0", "-i", str(HERE / "frames.txt")]
    filters = []
    for i, (name, at) in enumerate(marks, start=1):
        inputs += ["-i", str(HERE / "audio_pitch" / f"{name}.aiff")]
        d = int((at + LEAD) * 1000)
        filters.append(f"[{i}:a]adelay={d}|{d},aresample=48000[a{i}]")
    filters.append("".join(f"[a{i}]" for i in range(1, len(marks) + 1)) + f"amix=inputs={len(marks)}:normalize=0,apad[aout]")
    out = HERE / "snapsort_pitch.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
                    "-map", "0:v", "-map", "[aout]", "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-tune", "stillimage",
                    "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)], check=True)

    srt = []
    for n, (name, at) in enumerate(marks, start=1):
        start = at + LEAD
        srt += [str(n), f"{srt_time(start)} --> {srt_time(start + dur(HERE / 'audio_pitch' / f'{name}.aiff'))}", text[name], ""]
    (HERE / "snapsort_pitch.srt").write_text("\n".join(srt))
    print(f"wrote {out.name}: {total:.0f}s, {len(kept)} frames kept")


if __name__ == "__main__":
    main()
