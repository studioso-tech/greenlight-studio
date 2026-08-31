"""Assemble the demo video: real screenshots + generated narration, per cut.

Each cut = one still (a real screenshot from the recorded session) held for
exactly the length of its narration clip. Cuts 3/5 (the "agent trace" and
"reject a comparable" beats) still come from the same recording, just at a
single representative moment rather than continuous motion - the raw
Playwright takes were paced for a quick look-through, not for a 2-minute
narration read, so stretching a still is more honest than looping motion
that was never that long to begin with.

Usage:
    .venv-build/Scripts/python.exe scripts/assemble_demo.py
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "media" / "demo_takes_raw"
STILLS = RAW / "stills"
NARR = RAW / "narration"
OUT_DIR = ROOT / "media" / "demo_takes"
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1920, 1080

# (still image, narration wav, extra hold seconds after audio ends)
PLAN: list[tuple[str, str, float]] = [
    ("cut_01.png", "cut_01.wav", 0.3),
    ("cut_02.png", "cut_02.wav", 0.3),
    ("cut_03.png", "cut_03.wav", 0.3),
    ("cut_04.png", "cut_04.wav", 0.3),
    ("cut_05.png", "cut_05.wav", 0.3),
    ("cut_06.png", "cut_06.wav", 0.3),
    # cut 7 is split across two stills (EN metrics, then JA toggle) under one
    # narration track - handled separately below.
    # cut 8 (architecture diagram) added 2026-08-31, goes between cut 7 and
    # the closing tagline - handled in sequence below along with cut 9.
    ("cut_arch.png", "cut_08.wav", 0.3),
    ("cut_09_tagline.png", "cut_09.wav", 0.5),
]


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()


def make_still_segment(image: Path, audio: Path, extra_hold: float, out: Path) -> None:
    duration = wav_seconds(audio) + extra_hold
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-loop", "1", "-i", str(image),
            "-i", str(audio),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0xeef2f2",
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.3f}",
            str(out),
        ],
        check=True,
    )
    print(f"  {out.name}: {duration:.1f}s")


def make_split_segment(img_a: Path, img_b: Path, audio: Path, split_frac: float, out: Path) -> None:
    """cut 7: img_a for the first split_frac of the audio, img_b for the rest."""
    total = wav_seconds(audio) + 0.3
    t_a = total * split_frac
    t_b = total - t_a
    seg_a = out.with_name("_cut07a.mp4")
    seg_b = out.with_name("_cut07b.mp4")
    for img, dur, seg in ((img_a, t_a, seg_a), (img_b, t_b, seg_b)):
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-loop", "1", "-i", str(img),
                "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0xeef2f2",
                "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-t", f"{dur:.3f}",
                str(seg),
            ],
            check=True,
        )
    # concat the two silent video halves, then mux the one narration track over it
    listfile = out.with_name("_cut07_list.txt")
    listfile.write_text(f"file '{seg_a.name}'\nfile '{seg_b.name}'\n", encoding="utf-8")
    silent_joined = out.with_name("_cut07_silent.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", str(silent_joined)],
        check=True, cwd=out.parent,
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(silent_joined), "-i", str(audio),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)],
        check=True,
    )
    for f in (seg_a, seg_b, listfile, silent_joined):
        f.unlink(missing_ok=True)
    print(f"  {out.name}: {total:.1f}s (EN {t_a:.1f}s / JA {t_b:.1f}s)")


def main() -> None:
    print("Building per-cut segments...")
    segments: list[Path] = []

    for idx, (img_name, wav_name, hold) in enumerate(PLAN):
        out = OUT_DIR / f"seg_{img_name.split('.')[0]}.mp4"
        make_still_segment(STILLS / img_name, NARR / wav_name, hold, out)
        segments.append(out)
        if img_name == "cut_06.png":
            # cut 7 (split EN/JA) goes here, between 6 and 8
            seg7 = OUT_DIR / "seg_cut_07.mp4"
            make_split_segment(
                STILLS / "cut_07a_en.png", STILLS / "cut_07b_ja.png",
                NARR / "cut_07.wav", 0.62, seg7,
            )
            segments.append(seg7)

    print("\nConcatenating final video...")
    listfile = OUT_DIR / "_final_list.txt"
    listfile.write_text(
        "".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8"
    )
    final = OUT_DIR / "greenlight_studio_demo.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", str(final)],
        check=True, cwd=OUT_DIR,
    )
    listfile.unlink(missing_ok=True)

    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(final)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    d = float(dur)
    print(f"\n{final}")
    print(f"  {d:.1f}s = {int(d // 60)}m{int(d % 60):02d}s")


if __name__ == "__main__":
    main()
