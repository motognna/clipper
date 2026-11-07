"""Analiza video cu Gemini: alege momentele + scrie scripturile. Video-ul (proxy 480p) se încarcă o singură dată."""
import json
import os
import re
import time

from google import genai
from google.genai import types

from . import config, media, styles

MIN_SCORE = 7.0      # poarta de calitate: sub acest scor un moment nu se transformă în clip (doar momente virale de top)

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

        climax_t = None
        if "climax_time" in moment:
            try:
                ct = media.parse_seconds(str(moment["climax_time"]))
                if ct > 0:
                    climax_t = round(ct, 2)
            except Exception:
                pass

        return {
            "title": str(moment.get("title", "Insane moment")).strip()[:95],
            "hook_text": str(moment.get("hook_text", "WAIT FOR IT")).strip()[:40],
            "description": str(moment.get("description", "")).strip(),
            "tags": [t.lstrip("#") for t in tags][:12],
            "segments": [[round(a, 2), round(b, 2)] for a, b in segs],
            "voiceover_script": script,
            "pop_text": re.sub(r"[^A-Za-z0-9!?' ]", "", str(moment.get("pop_text", ""))).strip()[:14].upper(),
            "mid_pop": re.sub(r"[^A-Za-z0-9!?' ]", "", str(moment.get("mid_pop", ""))).strip()[:18].upper(),
            "climax_pop": re.sub(r"[^A-Za-z0-9!?' ]", "", str(moment.get("climax_pop", ""))).strip()[:14].upper(),
            "climax_time": climax_t,
            "archetype": str(moment.get("archetype", "")).strip()[:40],
            "score": float(moment.get("score", 10) or 0),
        }
    except Exception:
        return None


def _build_prompt(n: int, game: str, requirements: str, used: list, context: str = "") -> str:
    extra = ""
    if requirements:
        extra += (f"\nREQUESTED THEME: \"{requirements}\". Only select moments where this theme is CLEARLY and visibly "
                  f"present in the chosen segments. Do NOT stretch the theme or substitute something loosely related. "
                  f"If the footage contains fewer than {n} such moments, return FEWER objects; if it contains none, "
                  f"return an empty array []. Set \"matches_request\" to true only if the theme is really on screen.")
    if used:
        extra += ("\nThese time ranges were ALREADY used – do NOT pick moments overlapping them: "
                  + ", ".join(used))
    if context:
        extra += (f"\nCREATOR NOTES (the channel owner's own knowledge/opinion – weave it genuinely into the commentary "
                  f"of the relevant moments, in first person when natural, never invent facts beyond it): {context}")
    count = f"up to {n}" if requirements else f"exactly {n}"
    styles_list = "\n".join(f"  * {a}" for a in styles.ARCHETYPES)
    return f"""
You are a top YouTube Shorts editor. Watch this {game} gameplay video (PS5 footage) and select the {n} BEST moments
for viral vertical shorts (chaotic, funny, impressive, cinematic or beautiful).{extra}

FRAME-PERFECT TIMECODES & VISUAL PRECISION:
- Look at the TOP-LEFT corner of the video: a running visual timecode [HH:MM:SS.mmm] is permanently burned into every frame.
- ALWAYS read and use these EXACT visual numbers for start and end times to eliminate any sync drift!
- Editorial In/Out Rules:
  * In-Point: Segment 1 MUST start right on the action trigger (the exact frame a vehicle accelerates, a jump is launched, or chaos starts). ZERO seconds of boring walking, sitting in cars, or navigating menus.
  * Cut the fluff: Use jump-cuts between key beats. Do not show long straight driving or empty travel.
  * Payoff & Climax: The final segment must clearly show the apex payoff and cut ~0.5s after the outcome resolves.
  * "climax_time": Specify the exact visual timecode of the single most explosive or funny moment for the 1.2s opening teaser.

VOICEOVER STYLE – this is read by a TTS voice, so the text itself must carry the energy:
- Intense, punchy and dramatic like a hyped YouTuber, but with a NATURAL spoken rhythm (it will NOT be sped up).
- AGGRESSIVE & NATURAL HOOK (First 2 seconds):
  * The script MUST open with a loud, natural EXCLAMATION followed by an immediate high-stakes hook!
  * Exclamation examples: "WAIT!", "WHAT?!", "HOLD UP!", "BRO, LOOK AT THIS!", "NO WAY!", "ARE YOU KIDDING ME?!", "STOP SCROLLING!", "OH MY GOD!"
  * Immediately follow with an intense, natural reaction directly about what happens on screen.
    e.g.: "WAIT! Did he really just try that in broad daylight?!",
          "NO WAY! Watch what happens to this helicopter in three seconds!",
          "BRO, STOP! This might be the most illegal stunt in {game} history!"
- Use short sentences (3-10 words), exclamation marks, rhetorical questions, ALL-CAPS on 1-2 key words per script for
  emphasis, and natural reactions like "Wait, what?!", "Are you kidding me?!", "No way!".
- Build tension in the body (what is happening, what goes wrong), then a big payoff/punchline sentence.
- Describe ONLY what actually happens in the chosen segments. The voiceover must match the footage.
- Length: about {config.WORDS_PER_SEC:.1f} words per second of the total length of the segments you choose (a 30 s edit
  = ~84 words). The script must fill the whole clip so there is never silence at the end.
- Do NOT add any call-to-action like "follow", "like" or "subscribe" – it is appended automatically afterwards.
- No emojis, no hashtags, no stage directions, plain spoken sentences only.
- "hook_text" is the on-screen hook: 2-4 aggressive words in CAPS (e.g. "THIS IS INSANE", "NO WAY!", "WATCH THIS").

POP-UPS & RETENTION (Visual pacing across the video):
- "pop_text": 1-2 word opening exclamation in CAPS popping at second 0 (e.g. WAIT!, NO WAY?!, WHAT?!, LOOK!)
- "mid_pop": 2-3 word mid-tension exclamation popping around cut 1 or midway (e.g. WATCH THIS!, HOLD ON!, HERE IT COMES!, OH NO!)
- "climax_pop": 1-2 word payoff exclamation popping at the climax impact (e.g. INSANE!, BOOM!, HOW?!, UNREAL!, PERFECT!)
- "climax_time": exact timestamp (MM:SS) of the single most crazy, funny or explosive peak moment within your chosen segments (used for the 1.2s retention teaser at second 0)

NARRATIVE VARIETY – the clips of one batch must NOT feel templated. Give every moment a DIFFERENT narrative style from this
list (cycle through them, never use the same style twice in a row) and write the hook, the pacing and the ending in that
style, with different wording every time:
{styles_list}
Never reuse the same sentences, jokes or sentence structure across moments.

QUALITY: give each moment an honest "score" from 1 to 10 for originality and entertainment value (10 = unmissable,
5 = filler). Only return moments you would genuinely post on your own channel – prefer fewer, better moments. Sub-7 moments are rejected.

Return ONLY a JSON array with {count} objects:
[{{"title": "catchy YouTube title (max 80 chars)",
   "hook_text": "2-4 WORD ON-SCREEN HOOK IN CAPS",
   "pop_text": "1-2 word natural exclamation popping at second 0 (e.g. WAIT!, NO WAY?!)",
   "mid_pop": "2-3 word mid-tension pop (e.g. WATCH THIS!, HOLD ON!)",
   "climax_pop": "1-2 word climax pop (e.g. INSANE!, BOOM!)",
   "climax_time": "MM:SS timestamp of the peak moment",
   "archetype": "the NARRATIVE STYLE name you used (one of the list above)",
   "score": 8,
   "description": "1-2 sentence description with real context about what happens",
   "tags": ["gta6", "shorts", "..."],
   "matches_request": true,
   "evidence": "one short sentence describing what is visible on screen that matches the requested theme (empty if no theme requested)",
   "segments": [{{"start": "MM:SS", "end": "MM:SS"}}],
   "voiceover_script": "..."}}]
"""


def check_theme_material(client, model, video_file, requirements: str, game: str, log, stop) -> tuple:
    """Verificare strictă dacă există footage real pentru cerința specială cerută."""
    log(f"Verificare cerințe speciale: există material în video pentru „{requirements}”?...")
    prompt = f"""
You are a strict, no-nonsense video footage auditor. Inspect this {game} gameplay video carefully.
The user requested this specific theme / requirement: "{requirements}".

Your task is to determine whether this video actually contains clear, visible gameplay scenes matching "{requirements}".
- If the footage genuinely and clearly contains scenes of "{requirements}", return exists: true, and list the approximate timestamps (MM:SS) where it can be seen.
- If the footage does NOT contain "{requirements}" (or only contains unrelated gameplay like normal driving or walking), return exists: false, and explain what is actually shown in the video instead.
- Do NOT guess, do NOT hallucinate, and do NOT stretch the definition. Be completely strict.

Return ONLY a JSON object:
{{
  "exists": true,
  "reason": "1-2 sentence honest summary of what was found or why it does not exist",
  "timestamps": ["MM:SS"]
}}
"""
    try:
        resp = _generate(client, model, video_file, prompt, log, stop)
        data = _extract_json(resp.text)
        exists = bool(data.get("exists", False))
        reason = str(data.get("reason", "")).strip()
        return exists, reason
    except Exception as e:
        log(f"Atenție: verificarea preliminară a eșuat ({e}). Se continuă cu căutarea normală.")
        return True, ""


def _collect(client, model, video_file, info, n, game, requirements, used, log, stop, context="") -> list:
    """Cere până la n momente; întoarce doar momente valide (și, dacă s-a cerut o temă, doar cele care o respectă)."""
    found, used = [], list(used)
    remaining = n
    stalls = 0
    max_stalls = 1 if requirements else 3        # cu temă: dacă Gemini nu mai găsește nimic, ne oprim repede
    while remaining > 0 and stalls < max_stalls:
        batch = min(config.GEMINI_BATCH, remaining)
        resp = _generate(client, model, video_file, _build_prompt(batch, game, requirements, used, context), log, stop)
        try:
            raw = _extract_json(resp.text)
        except Exception as e:
            log(f"Răspuns invalid de la Gemini ({e}); reîncerc.")
            stalls += 1
            continue
        added = 0
        for m in raw if isinstance(raw, list) else []:
            if requirements and not (m.get("matches_request", False) and str(m.get("evidence", "")).strip()):
                continue                          # momentul nu conține tema cerută -> îl ignorăm
            v = _validate(m, info["duration"])
            if not v:
                continue
            if v["score"] < MIN_SCORE:
                log(f"  moment respins (scor calitate {v['score']:.0f} < {MIN_SCORE:.0f}): {v['title']}")
                continue
            span = (v["segments"][0][0], v["segments"][-1][1])
            if any(not (span[1] <= x["segments"][0][0] or span[0] >= x["segments"][-1][1]) for x in found):
                continue                          # se suprapune cu un moment deja ales
            found.append(v)
            used.append(f"{media.fmt_mmss(span[0])}-{media.fmt_mmss(span[1])}")
            added += 1
            remaining -= 1
            if remaining == 0:
                break
        stalls = 0 if added else stalls + 1
        log(f"  -> {len(found)}/{n} momente" + (f" cu tema „{requirements}”" if requirements else ""))
    return found


def find_moments(video_path: str, n: int, requirements: str, used_ranges: list,
                 game: str = config.DEFAULT_GAME, log=print, stop=lambda: False, warn=None,
                 context: str = "") -> list:
    """Alege momentele. Dacă tema cerută lipsește din footage, avertizează utilizatorul și
    completează clipurile automat, fără temă specifică."""
    warn = warn or log
    info = media.probe(video_path)
    client = genai.Client(api_key=config.load_api_key())
    video_file = _upload(client, video_path, log, stop)
    model = _pick_model(client)
    log(f"Model Gemini: {model}")

    requirements = requirements.strip()
    moments = []
    if requirements:
        exists, reason = check_theme_material(client, model, video_file, requirements, game, log, stop)
        if not exists:
            msg = (f"Cerința specială „{requirements}” NU a fost găsită în footage.\n"
                   f"Motiv: {reason or 'Nu există secvențe video relevante'}.\n\n"
                   f"Generez automat cele mai bune {n} momente din video, fără cerințe speciale.")
            warn(msg)
            log(f"⚠ {msg}")
            requirements = ""  # Se trece la generare automată fără temă specifică!
        else:
            log(f"✓ Material confirmat pentru „{requirements}” ({reason}).")

    if requirements:
        log(f"Se caută momente cu tema: „{requirements}”...")
        moments = _collect(client, model, video_file, info, n, game, requirements, used_ranges, log, stop, context)
        for m in moments:
            m["themed"] = True
        if len(moments) < n:
            if not moments:
                warn(f"⚠ Deși s-a detectat material, nu s-au putut extrage clipuri complete pentru tema „{requirements}”. "
                     f"Generez {n} clipuri automat, fără temă specifică.")
            else:
                warn(f"⚠ S-au extras doar {len(moments)} din {n} momente cu tema „{requirements}”. "
                     f"Restul de {n - len(moments)} le generez automat, fără temă specifică.")

    remaining = n - len(moments)
    if remaining > 0:
        used = list(used_ranges) + [f"{media.fmt_mmss(m['segments'][0][0])}-{media.fmt_mmss(m['segments'][-1][1])}"
                                    for m in moments]
        log(f"Se aleg {remaining} momente automat (cele mai bune din video)...")
        auto = _collect(client, model, video_file, info, remaining, game, "", used, log, stop, context)
        for m in auto:
            m["themed"] = False
        moments += auto

    # Calibrare fină a cadrelor: snap la cel mai apropiat impact audio (sincronizare ritmică)
    for m in moments:
        new_segs = []
        for a, b in m.get("segments", []):
            a_snapped = media.snap_to_beat(video_path, a, window=0.6)
            new_segs.append([round(a_snapped, 2), round(b, 2)])
        m["segments"] = new_segs
        if m.get("climax_time"):
            m["climax_time"] = round(media.snap_to_beat(video_path, m["climax_time"], window=0.5), 2)

    return moments

