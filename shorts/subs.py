"""Subtitrări karaoke (cuvânt cu cuvânt, cu 'pop') + titlu hook animat, în format ASS."""
import re

from . import config

_WHISPER = None

ACTIVE = "&H0000E6FF&"   # auriu (BGR)
NORMAL = "&H00FFFFFF&"   # alb


def get_whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel("small.en", device="cpu", compute_type="int8")
    return _WHISPER


def _t(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _word(w: str) -> str:
    w = w.strip().strip('",;:()')
    return w.upper()


def _chunks(words, max_words=3, max_chars=17):
    chunk, chars = [], 0
    for w in words:
        if chunk and (len(chunk) >= max_words or chars + len(w["t"]) + 1 > max_chars):
            yield chunk
            chunk, chars = [], 0
        chunk.append(w)
        chars += len(w["t"]) + 1
        if re.search(r"[.!?]$", w["t"]):
            yield chunk
            chunk, chars = [], 0
    if chunk:
        yield chunk


def build_ass(audio_path: str, hook_text: str, ass_path: str, video_dur: float):
    segments, _ = get_whisper().transcribe(audio_path, word_timestamps=True, language="en")
    words = []
    for seg in segments:
        for w in (seg.words or []):
            t = _word(w.word)
            if t:
                words.append({"t": t, "s": float(w.start), "e": float(w.end)})

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.OUT_W}
PlayResY: {config.OUT_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Montserrat ExtraBold,88,{NORMAL},{NORMAL},&H00000000&,&H96000000&,-1,0,0,0,100,100,1,0,1,8,3,2,60,60,540,1
Style: Hook,Montserrat Black,104,&H00FFFFFF&,&H00FFFFFF&,&H000000C8&,&H96000000&,-1,0,0,0,100,100,2,0,1,10,4,8,70,70,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]

    # Titlu hook (primele ~3s)
    hook = re.sub(r"[{}\\]", "", hook_text).upper().strip()
    if hook:
        # rupe pe 2 rânduri dacă e lung
        parts = hook.split()
        if len(parts) > 3:
            mid = (len(parts) + 1) // 2
            hook = " ".join(parts[:mid]) + "\\N" + " ".join(parts[mid:])
        lines.append(
            f"Dialogue: 1,{_t(0.0)},{_t(3.2)},Hook,,0,0,0,,"
            r"{\fad(120,300)\fscx55\fscy55\t(0,200,\fscx108\fscy108)\t(200,320,\fscx100\fscy100)}" + hook + "\n"
        )

    # Karaoke
    for chunk in _chunks(words):
        for i, w in enumerate(chunk):
            start = w["s"]
            end = chunk[i + 1]["s"] if i + 1 < len(chunk) else max(w["e"], start + 0.15)
            parts = []
            for j, x in enumerate(chunk):
                if j == i:
                    parts.append(r"{\c" + ACTIVE + r"\fscx122\fscy122\t(0,90,\fscx100\fscy100)}" + x["t"] + r"{\r}")
                else:
                    parts.append(r"{\c" + NORMAL + "}" + x["t"])
            lines.append(f"Dialogue: 0,{_t(start)},{_t(end)},Sub,,0,0,0,,{' '.join(parts)}\n")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
