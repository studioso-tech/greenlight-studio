"""Synthesize the demo video's English narration, one clip per storyboard cut.

Per-cut (not one continuous blob) so the final ffmpeg assembly can match each
clip's actual duration against the recorded screen action it narrates -
same principle CurricuShift/Wayfinder's make_narration.py used.

Usage:
    .venv-mcp/Scripts/python.exe scripts/make_narration.py
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.genai import types  # noqa: E402
from app.llm import genai_client  # noqa: E402

OUT_DIR = ROOT / "media" / "demo_takes_raw" / "narration"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TTS_MODEL = "gemini-3.1-flash-tts-preview"
VOICE = "Leda"  # clear, calm young female voice - the established project standard
                 # (used for the Wayfinder/CurricuShift demo narration; a prior
                 # take here wrongly used "Orus", a male-leaning voice - fixed
                 # 2026-08-31 per user correction)
SAMPLE_RATE = 24000

STYLE = (
    "Grounded and confident, like a studio head laying out a case to a "
    "committee. Steady, measured, unhurried - around 150 words per minute, "
    "with natural pauses at commas and full stops. Not a sales pitch, not "
    "an advertisement.\n\n"
    "Read the following line for a product demo video. Do not read markup.\n\n"
)

# Cut number -> narration text. Cut 7 is the shortened version approved
# 2026-08-31 (judges are English speakers; the Japanese scene only needs to
# demonstrate that the language exists, not dwell on it).
CUTS: dict[int, str] = {
    1: (
        "A studio decides whether to spend fifty million dollars in a room, "
        "in an afternoon. The people in that room are experienced. They are "
        "also guessing. The record of what happened to films like this one "
        "exists. It is just never in the room."
    ),
    2: "Greenlight Studio puts it there. Paste a screenplay, a treatment, or a series bible.",
    3: (
        "Three Gemini agents, built on Google's Agent Development Kit. The "
        "first reads the material. The second investigates — and this "
        "is the part that matters. It is not running a fixed set of "
        "queries. It decides what to ask, writes the SQL itself, and sends "
        "it to ClickHouse through ClickHouse's own MCP server."
    ),
    4: (
        "Then it writes the memo. Every figure is computed in Python from "
        "rows the database returned. The model never does the arithmetic, "
        "so the prose and the chart cannot disagree. It cites real titles "
        "with real numbers, and it says where it might be wrong."
    ),
    5: (
        "A committee argues, so you can too. Any comparable can be "
        "rejected. \"That title is not comparable\" is the most common "
        "objection in the room. Untick it, and the verdict re-derives from "
        "ClickHouse in milliseconds, with no model call at all. And the "
        "score is not a verdict from nowhere. This is what it is made of."
    ),
    # Rewritten 2026-08-31 to match this recording's actual ladder: the
    # verdict only shifts in one narrow band (fifteen to twenty million) and
    # reverts everywhere else, including well below that band. "Just cut the
    # budget" is not simply true here - real screen, real number, checked
    # against the still before this was finalized.
    6: (
        "The obvious advice is \"reduce the budget\", so it checks. The "
        "verdict only improves in one narrow band, and reverts everywhere "
        "else — including well below that band. There is no simple line "
        "from budget to verdict here. That is a more useful answer than a "
        "recommendation."
    ),
    # Shortened 2026-08-31: judges are English speakers, so this scene only
    # needs to demonstrate Japanese support exists, not dwell on it.
    7: (
        "Television is judged differently — not on return, but on "
        "whether it comes back, against the market it was made for. And "
        "the same tool speaks Japanese, reaching the deeper "
        "English-language record without ever leaving it."
    ),
    # Added 2026-08-31: architecture beat, placed after the product cuts and
    # before the closing tagline.
    8: (
        "Three Gemini agents run stateless on Cloud Run. The catalogue "
        "lives on one self-hosted ClickHouse node, reached only through "
        "its own MCP server, inside the VPC. Every number in the memo "
        "traces back to a row in that database."
    ),
    9: "Decide with the record. Not the room.",
}


def synth(num: int, script: str) -> Path:
    out = OUT_DIR / f"cut_{num:02d}.wav"
    client = genai_client()
    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=STYLE + script,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
                )
            ),
        ),
    )
    part = response.candidates[0].content.parts[0].inline_data
    pcm = part.data
    with wave.open(str(out), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm)
    (OUT_DIR / f"cut_{num:02d}.txt").write_text(script, encoding="utf-8")
    seconds = len(pcm) / 2 / SAMPLE_RATE
    words = len(script.split())
    print(f"  cut {num}: {seconds:5.1f}s  {words:3d} words  {words/seconds*60:5.1f} wpm")
    return out


def main() -> int:
    print(f"Synthesizing {len(CUTS)} narration cuts ({TTS_MODEL} / {VOICE})")
    total = 0.0
    for num in sorted(CUTS):
        out = synth(num, CUTS[num])
        with wave.open(str(out), "rb") as f:
            total += f.getnframes() / f.getframerate()
    print(f"\ntotal (unsped): {total:.1f}s = {int(total//60)}m{int(total%60):02d}s")
    print(f"saved to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
