"""Analiza video cu Gemini: alege momentele + scrie scripturile. Video-ul (proxy 480p) se încarcă o singură dată."""
import json
import os
import re
import time

from google import genai
from google.genai import types

from . import config, media

MODELS = ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.0-flash"]
_UPLOAD_CACHE = os.path.join(config.CACHE_DIR, "uploads.json")


def _load_cache() -> dict:
    try:
        with open(_UPLOAD_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c: dict):
    with open(_UPLOAD_CACHE, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)


def _upload(client, video_path: str, log, stop):
    key = media.video_key(video_path)
    cache = _load_cache()
    if key in cache:
        try:
            f = client.files.get(name=cache[key])
            if f.state.name == "ACTIVE":
                log("Video deja încărcat în Gemini – refolosit (fără re-upload).")
                return f
        except Exception:
            pass
    proxy = media.make_proxy(video_path, log)
    log(f"Se încarcă proxy-ul ({os.path.getsize(proxy) / 1e6:.0f} MB) în Gemini...")
    f = client.files.upload(file=proxy)
    while f.state.name == "PROCESSING":
        if stop():
            raise InterruptedError("Oprit de utilizator.")
        log("Procesare pe serverele Google...")
        time.sleep(8)
        f = client.files.get(name=f.name)
    if f.state.name != "ACTIVE":
        raise RuntimeError(f"Fișierul a ajuns în starea {f.state.name}")
    cache[key] = f.name
    _save_cache(cache)
    return f


def _pick_model(client) -> str:
    for m in MODELS:
        try:
            client.models.generate_content(model=m, contents="ok")
            return m
        except Exception:
            continue
    return MODELS[1]


def _generate(client, model, video_file, prompt, log, stop):
    delay = 20
    for attempt in range(5):
        if stop():
            raise InterruptedError("Oprit de utilizator.")
        try:
            return client.models.generate_content(
                model=model,
                contents=[video_file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.5,
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
                ),
            )
        except Exception as e:
            msg = str(e)
            if any(c in msg for c in ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE")) and attempt < 4:
                log(f"Gemini a refuzat temporar cererea (limită/încărcare). Reîncerc în {delay}s...")
                time.sleep(delay)
                delay *= 2
                continue
            raise


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def _validate(moment: dict, video_dur: float):
    """Normalizează un moment; întoarce None dacă e inutilizabil."""
    try:
        segs = []
        for s in moment.get("segments", []):
            a, b = media.parse_seconds(s["start"]), media.parse_seconds(s["end"])
            a, b = max(0.0, a), min(video_dur, b)
            if b - a >= 2.0:
                segs.append((a, b))
        if not segs and "start_time" in moment:
            a, b = media.parse_seconds(moment["start_time"]), min(video_dur, media.parse_seconds(moment["end_time"]))
            if b - a >= 2.0:
                segs.append((a, b))
        if not segs:
            return None
        total = sum(b - a for a, b in segs)
        if total > config.MAX_CLIP_SEC:  # taie surplusul din ultimul segment
            extra = total - config.MAX_CLIP_SEC
            a, b = segs[-1]
            segs[-1] = (a, max(a + 2.0, b - extra))
        script = str(moment.get("voiceover_script", "")).strip()
        if len(script.split()) < 8:
            return None
        tags = moment.get("tags", [])
        if isinstance(tags, str):
            tags = re.findall(r"#?(\w+)", tags)
        return {
            "title": str(moment.get("title", "Insane moment")).strip()[:95],
            "hook_text": str(moment.get("hook_text", "WAIT FOR IT")).strip()[:40],
            "description": str(moment.get("description", "")).strip(),
            "tags": [t.lstrip("#") for t in tags][:12],
            "segments": [[round(a, 2), round(b, 2)] for a, b in segs],
            "voiceover_script": script,
        }
    except Exception:
        return None


def _build_prompt(n: int, game: str, requirements: str, used: list) -> str:
    extra = ""
    if requirements:
        extra += (f"\nMANDATORY user requirements (every moment MUST respect them, prefer these over generic action): "
                  f"{requirements}")
    if used:
        extra += ("\nThese time ranges were ALREADY used – do NOT pick moments overlapping them: "
                  + ", ".join(used))
    return f"""
You are a top YouTube Shorts editor. Watch this {game} gameplay video (PS5 footage) and select the {n} BEST moments
for viral vertical shorts (chaotic, funny, impressive, cinematic or beautiful).{extra}

For each moment build a tight edit: 1 to 3 consecutive-in-story segments (use several segments to CUT OUT dead time and
make jump-cuts; total 20-40 seconds). Timestamps must be exact (MM:SS, relative to the video start) and every segment must
start right before the action begins and end right after it resolves.

VOICEOVER STYLE – this is read by a TTS voice, so the text itself must carry the energy:
- Sound like a hyped-up, adrenaline-fueled YouTuber, NOT a calm narrator. Fast, loud, emotional, a bit dramatic.
- The FIRST sentence is an aggressive HOOK (max 8 words) that grabs the viewer by the throat in the first second, e.g. a
  command, a shock statement or a challenge: "Stop scrolling, this is INSANE!", "This should NOT be possible!",
  "Nobody saw this coming!", "You will NOT believe what he just did!". Never start with "In this video" or a greeting.
- Use very short punchy sentences (3-10 words), exclamation marks, rhetorical questions, ALL-CAPS on 1-2 key words per
  script for emphasis, and reactions like "Wait, what?!", "Are you kidding me?!", "No way!".
- Build tension in the body (what is happening, what goes wrong), then a huge payoff/punchline sentence.
- Length: about {config.WORDS_PER_SEC:.1f} words per second of the total length of the segments you choose (a 30 s edit
  = ~90 words). The script must fill the whole clip so there is never silence at the end.
- Do NOT add any call-to-action like "follow", "like" or "subscribe" – it is appended automatically afterwards.
- No emojis, no hashtags, no stage directions, plain spoken sentences only. Do not claim anything not visible in the video.
- "hook_text" is the on-screen hook: 2-4 aggressive words in CAPS (e.g. "THIS IS INSANE", "NO WAY!", "WATCH THIS").

Return ONLY a JSON array with exactly {n} objects:
[{{"title": "catchy YouTube title (max 80 chars)",
   "hook_text": "2-5 WORD ON-SCREEN HOOK IN CAPS",
   "description": "1-2 sentence description",
   "tags": ["gta6", "shorts", "..."],
   "segments": [{{"start": "MM:SS", "end": "MM:SS"}}],
   "voiceover_script": "..."}}]
"""


def find_moments(video_path: str, n: int, requirements: str, used_ranges: list,
                 game: str = config.DEFAULT_GAME, log=print, stop=lambda: False) -> list:
    info = media.probe(video_path)
    client = genai.Client(api_key=config.load_api_key())
    video_file = _upload(client, video_path, log, stop)
    model = _pick_model(client)
    log(f"Model Gemini: {model}")

    moments, used = [], list(used_ranges)
    remaining = n
    stalls = 0
    while remaining > 0 and stalls < 3:
        batch = min(config.GEMINI_BATCH, remaining)
        log(f"Se cer {batch} momente de la Gemini ({len(moments)}/{n} găsite)...")
        resp = _generate(client, model, video_file, _build_prompt(batch, game, requirements, used), log, stop)
        try:
            raw = _extract_json(resp.text)
        except Exception as e:
            log(f"Răspuns invalid de la Gemini ({e}); reîncerc.")
            stalls += 1
            continue
        added = 0
        for m in raw:
            v = _validate(m, info["duration"])
            if not v:
                continue
            # evită suprapunerile cu ce avem deja
            span = (v["segments"][0][0], v["segments"][-1][1])
            if any(not (span[1] <= a or span[0] >= b) for a, b in
                   [(s[0][0], s[-1][1]) for s in [x["segments"] for x in moments]]):
                continue
            moments.append(v)
            used.append(f"{media.fmt_mmss(span[0])}-{media.fmt_mmss(span[1])}")
            added += 1
            remaining -= 1
            if remaining == 0:
                break
        stalls = 0 if added else stalls + 1
    return moments
