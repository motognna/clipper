"""Subtitrări karaoke (cuvânt cu cuvânt, cu 'pop'), titlu hook animat și 'pop' cu exclamație, în format ASS."""
import re
import threading

from . import config, styles

_whisper_lock = threading.Lock()
_WHISPER = None
NORMAL = "&H00FFFFFF&"   # alb


def get_whisper():
    global _WHISPER
    if _WHISPER is None:
        with _whisper_lock:
            if _WHISPER is None:
                from faster_whisper import WhisperModel
                _WHISPER = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=4)
    return _WHISPER


def _t(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _word(w: str) -> str:
    return w.strip().strip('",;:()').upper()


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


def build_ass(audio_path: str, hook_text: str, ass_path: str, video_dur: float,
              style: dict = None, pop_text: str = "WAIT!",
              mid_pop: str = "", climax_pop: str = "",
              teaser_active: bool = False, teaser_dur: float = 0.0, teaser_badge: str = ""):
    style = style or styles.STYLES[0]
    accent = style["accent_ass"]
    teaser_badge = teaser_badge or (styles.TEASER_BADGES[0] if teaser_active else "")
    segments, _ = get_whisper().transcribe(audio_path, word_timestamps=True, language="en", beam_size=1)
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
Style: Sub,Montserrat ExtraBold,88,{NORMAL},{NORMAL},&H00000000&,&H96000000&,-1,0,0,0,100,100,1,0,1,8,3,2,60,60,{style['sub_margin']},1
Style: Hook,Montserrat Black,104,&H00FFFFFF&,&H00FFFFFF&,&H000000C8&,&H96000000&,-1,0,0,0,100,100,2,0,1,10,4,8,70,70,170,1
Style: Pop,Montserrat Black,96,&H00101010&,&H00101010&,{accent},&H00000000&,-1,0,0,0,100,100,0,0,3,18,0,5,20,20,0,1
Style: PopMid,Montserrat Black,86,&H00101010&,&H00101010&,&H00FFFFFF&,&H00000000&,-1,0,0,0,100,100,0,0,3,16,0,5,20,20,0,1
Style: PopClimax,Montserrat Black,108,&H00FFFFFF&,&H00FFFFFF&,{accent},&H00000000&,-1,0,0,0,100,100,1,0,3,22,0,5,20,20,0,1
Style: Teaser,Montserrat Black,76,&H00FFFFFF&,&H00FFFFFF&,&H000018E0&,&H00000000&,-1,0,0,0,100,100,2,0,3,16,0,8,40,40,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]

    # 1. Badge pentru Climax Teaser (0.0s -> teaser_dur)
    if teaser_active and teaser_dur > 0.3:
        b_text = re.sub(r"[{}\\]", "", teaser_badge).upper().strip()
        lines.append(
            f"Dialogue: 3,{_t(0.05)},{_t(max(0.1, teaser_dur - 0.05))},Teaser,,0,0,0,,"
            r"{\pos(540,240)\fscx100\fscy100\t(0,250,\fscx112\fscy112)\t(250,500,\fscx100\fscy100)"
            r"\t(500,750,\fscx112\fscy112)\t(750,1000,\fscx100\fscy100)}" + b_text + "\n"
        )

    # 2. Titlu hook animat (la începutul poveștii)
    hook_start = teaser_dur if teaser_active else 0.0
    hook_end = min(video_dur, hook_start + 3.2)
    hook = re.sub(r"[{}\\]", "", hook_text).upper().strip()
    if hook:
        parts = hook.split()
        if len(parts) > 3:
            mid = (len(parts) + 1) // 2
            hook = " ".join(parts[:mid]) + "\\N" + " ".join(parts[mid:])
        lines.append(
            f"Dialogue: 1,{_t(hook_start)},{_t(hook_end)},Hook,,0,0,0,,"
            r"{\fad(120,300)\fscx55\fscy55\t(0,200,\fscx108\fscy108)\t(200,320,\fscx100\fscy100)}" + hook + "\n"
        )

    # 3. Pop #1: Hook Exclamație (la deschiderea poveștii)
    pop1 = re.sub(r"[{}\\]", "", pop_text).upper().strip() or "WAIT!"
    p1_start = hook_start + 0.05
    p1_end = min(video_dur, p1_start + 1.65)
    x1, y1, rot1 = style["pop_x"], style["pop_y"], style["pop_rot"]
    lines.append(
        f"Dialogue: 2,{_t(p1_start)},{_t(p1_end)},Pop,,0,0,0,,"
        + "{" + rf"\pos({x1},{y1})\frz{rot1}\fscx0\fscy0\fad(0,220)"
        + rf"\t(0,110,\fscx130\fscy130)\t(110,220,\fscx100\fscy100)"
        + rf"\t(300,380,\frz{rot1 + 4})\t(380,460,\frz{rot1 - 4})\t(460,540,\frz{rot1})" + "}" + pop1 + "\n"
    )

    # 4. Pop #2: Mid-tension Pop (la ~42% din durată)
    if mid_pop and video_dur > 14.0:
        p2_start = max(p1_end + 1.5, hook_start + (video_dur - hook_start) * 0.42)
        p2_end = min(video_dur - 3.5, p2_start + 1.35)
        if p2_end > p2_start + 0.6:
            pop2 = re.sub(r"[{}\\]", "", mid_pop).upper().strip()
            x2 = style.get("mid_x", 220)
            y2 = style.get("mid_y", 600)
            rot2 = style.get("mid_rot", 8)
            lines.append(
                f"Dialogue: 2,{_t(p2_start)},{_t(p2_end)},PopMid,,0,0,0,,"
                + "{" + rf"\pos({x2},{y2})\frz{rot2}\fscx0\fscy0\fad(0,180)"
                + rf"\t(0,90,\fscx125\fscy125)\t(90,180,\fscx100\fscy100)"
                + rf"\t(260,330,\frz{rot2 - 3})\t(330,400,\frz{rot2})" + "}" + pop2 + "\n"
            )

    # 5. Pop #3: Climax Payoff Pop (la momentul culminant, ~80% din durată)
    if climax_pop and video_dur > 16.0:
        p3_start = max(hook_start + 8.0, video_dur - 4.8)
        p3_end = min(video_dur - 0.5, p3_start + 1.5)
        if p3_end > p3_start + 0.6:
            pop3 = re.sub(r"[{}\\]", "", climax_pop).upper().strip()
            x3 = style.get("climax_x", 540)
            y3 = style.get("climax_y", 500)
            rot3 = style.get("climax_rot", -4)
            lines.append(
                f"Dialogue: 2,{_t(p3_start)},{_t(p3_end)},PopClimax,,0,0,0,,"
                + "{" + rf"\pos({x3},{y3})\frz{rot3}\fscx0\fscy0\fad(0,250)"
                + rf"\t(0,120,\fscx145\fscy145)\t(120,240,\fscx100\fscy100)"
                + rf"\t(320,400,\frz{rot3 + 5})\t(400,480,\frz{rot3 - 4})\t(480,560,\frz{rot3})" + "}" + pop3 + "\n"
            )

    # 6. Karaoke cuvânt cu cuvânt
    for chunk in _chunks(words, max_words=style["chunk"]):
        for i, w in enumerate(chunk):
            start = w["s"]
            end = chunk[i + 1]["s"] if i + 1 < len(chunk) else max(w["e"], start + 0.15)
            parts = []
            for j, x_ in enumerate(chunk):
                if j == i:
                    parts.append(r"{\c" + accent + r"\fscx122\fscy122\t(0,90,\fscx100\fscy100)}" + x_["t"] + r"{\r}")
                else:
                    parts.append(r"{\c" + NORMAL + "}" + x_["t"])
            lines.append(f"Dialogue: 0,{_t(start)},{_t(end)},Sub,,0,0,0,,{' '.join(parts)}\n")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
