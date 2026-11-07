import os
import re
import time
import json
import asyncio
import subprocess
from google import genai
from google.genai import types
import edge_tts
from gtts import gTTS
from faster_whisper import WhisperModel

# ================= CONFIGURARE =================
def load_api_key() -> str:
    # 1. Variabilă de mediu dacă este setată
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    # 2. Fișier .env local
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val

    # 3. Fișier api_key.txt
    txt_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")
    if os.path.exists(txt_file):
        with open(txt_file, "r", encoding="utf-8") as f:
            val = f.read().strip()
            if val:
                return val

    raise ValueError(
        "Nu s-a găsit cheia Gemini API!\n"
        "Creează fișierul '.env' cu linia:\n"
        "GEMINI_API_KEY=cheia_ta\n"
        "sau setează variabila de mediu GEMINI_API_KEY."
    )


GEMINI_API_KEY = load_api_key()
INPUT_VIDEO = "gameplay.mp4"
OUTPUT_DIR = "output_shorts"
PLAN_CACHE_FILE = os.path.join(OUTPUT_DIR, "clips_plan.json")
VOICE = "en-US-GuyNeural"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_existing_clips() -> tuple:
    """Returnează (următorul id liber, lista intervalelor deja folosite) din meta_*.json existente."""
    max_id = 0
    used = []
    for name in os.listdir(OUTPUT_DIR):
        m = re.fullmatch(r"(?:meta|short)_(\d+)\.(?:json|mp4)", name)
        if m:
            max_id = max(max_id, int(m.group(1)))
        if name.startswith("meta_") and name.endswith(".json"):
            try:
                with open(os.path.join(OUTPUT_DIR, name), "r", encoding="utf-8") as f:
                    c = json.load(f)
                used.append(f"{c['start_time']}-{c['end_time']}")
            except Exception:
                pass
    return max_id + 1, used


def ask_user() -> tuple:
    while True:
        raw = input("Câte clipuri vrei să generezi? [3]: ").strip() or "3"
        if raw.isdigit() and int(raw) > 0:
            n = int(raw)
            break
        print(" -> Introdu un număr întreg pozitiv.")
    req = input("Cerințe speciale (ex: clip cu mașini neapărat) [Enter = niciuna]: ").strip()
    return n, req


# 1. ANALIZA VIDEO CU GEMINI
def analyze_gameplay(video_path: str, n: int, requirements: str, used_ranges: list, start_id: int) -> list:
    client = genai.Client(api_key=GEMINI_API_KEY)

    candidate_models = ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.0-flash"]
    selected_model = "gemini-2.5-flash"
    for m in candidate_models:
        try:
            client.models.generate_content(model=m, contents="ok")
            selected_model = m
            break
        except Exception:
            continue

    print(f"[1/5] Se încarcă '{video_path}' în Gemini ({selected_model})...")
    video_file = client.files.upload(file=video_path)
    while video_file.state.name == "PROCESSING":
        print(" -> Procesare pe serverele Google (15-30s)...")
        time.sleep(10)
        video_file = client.files.get(name=video_file.name)

    extra = ""
    if requirements:
        extra += f"\n    MANDATORY REQUIREMENTS from the user (follow them strictly): {requirements}"
    if used_ranges:
        extra += ("\n    These time ranges were ALREADY used, do NOT pick moments overlapping them: "
                  + ", ".join(used_ranges))

    prompt = f"""
    Watch this GTA V gameplay video. Find the TOP {n} most chaotic, funny, or impressive moments (20 to 35 seconds each).{extra}
    Write a fast-paced English 'Micro-Storytelling' voiceover script (~2.8 words/sec) with a 3s Hook, Body, and looping Ending.
    Return ONLY a JSON array with exactly {n} items:
    [{{"id": 1, "start_time": "MM:SS", "end_time": "MM:SS", "duration_sec": 25, "title": "Title #gta5 #shorts", "voiceover_script": "..."}}]
    """

    response = client.models.generate_content(
        model=selected_model,
        contents=[video_file, prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4)
    )

    raw_text = response.text.strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.startswith("```"):
        raw_text = raw_text[3:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]

    clips_data = json.loads(raw_text.strip())[:n]
    # ID-uri noi, ca să nu suprascriem clipurile din batch-urile anterioare
    for i, clip in enumerate(clips_data):
        clip["id"] = start_id + i
    with open(os.path.join(OUTPUT_DIR, f"clips_plan_{start_id}.json"), "w", encoding="utf-8") as f:
        json.dump(clips_data, f, indent=2)
    return clips_data


# 2. GENERARE VOICEOVER (TTS BLINDAT CU RETRY + FALLBACK)
async def generate_tts(text: str, output_audio: str):
    # Curățăm caracterele care blochează parserul SSML din edge-tts
    clean_text = re.sub(r'[*_#<>~`"\[\]]', '', str(text)).strip()
    clean_text = clean_text.replace("&", " and ").replace("...", ". ")

    if not clean_text:
        clean_text = "Watch this insane moment in Grand Theft Auto!"

    # Încercăm vocile neurale din Edge cu retry
    for voice_candidate in ["en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-AriaNeural"]:
        for attempt in range(2):
            try:
                communicate = edge_tts.Communicate(clean_text, voice_candidate, rate="+10%")
                await communicate.save(output_audio)
                if os.path.exists(output_audio) and os.path.getsize(output_audio) > 1000:
                    return
            except Exception as e:
                print(f" -> [Retry TTS] {voice_candidate} a dat eroare ({e}), reîncercăm...")
                await asyncio.sleep(1.5)

    # Fallback garantat 100% dacă serverul Microsoft Edge refuză conexiunea
    print(" -> [Fallback] Se folosește Google TTS (gTTS)...")
    tts = gTTS(text=clean_text, lang="en", slow=False)
    tts.save(output_audio)

# 3. GENERARE SUBTITRĂRI DINAMICE (.ASS)
def generate_ass_subtitles(audio_path: str, ass_path: str, whisper_model: WhisperModel):
    segments, _ = whisper_model.transcribe(audio_path, word_timestamps=True)

    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,DejaVu Sans,78,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,50,50,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    lines = [ass_header]
    words_buffer = []
    for segment in segments:
        if not segment.words:
            continue
        for w in segment.words:
            words_buffer.append(w)
            if len(words_buffer) == 2:
                start_t = fmt_time(words_buffer[0].start)
                end_t = fmt_time(words_buffer[-1].end)
                clean_text = " ".join([item.word.strip().upper() for item in words_buffer])
                lines.append(f"Dialogue: 0,{start_t},{end_t},Shorts,,0,0,0,,{clean_text}\n")
                words_buffer = []

    if words_buffer:
        start_t = fmt_time(words_buffer[0].start)
        end_t = fmt_time(words_buffer[-1].end)
        clean_text = " ".join([item.word.strip().upper() for item in words_buffer])
        lines.append(f"Dialogue: 0,{start_t},{end_t},Shorts,,0,0,0,,{clean_text}\n")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# 4. RANDARE FFMPEG (GARANTAT FARA EROARE DE CROP PE ORICE REZOLUTIE)
def parse_seconds(ts: str) -> float:
    parts = [float(p) for p in str(ts).strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return float(parts[0])


def render_short(input_video: str, start_time: str, end_time: str, audio_path: str, ass_path: str, output_path: str):
    start_sec = parse_seconds(start_time)
    end_sec = parse_seconds(end_time)
    duration_sec = max(5.0, end_sec - start_sec)

    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")

    # Filtru matematic infailibil:
    # 1. [bg]: Scalează cu force_original_aspect_ratio=increase la 1080x1920, apoi face crop exact 1080x1920 și blur
    # 2. [fg]: Scalează la lățimea 1080px păstrând proporțiile originale (fără crop care să poată da eroare!)
    # 3. Suprapune [fg] pe centrul lui [bg] și aplică subtitrările .ass
    filter_complex = (
        f"[0:v]split=2[v1][v2];"
        f"[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
        f"[v2]scale=1080:1080:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass='{ass_escaped}',format=yuv420p[vout];"
        f"[0:a]volume=0.15[a_game];"
        f"[1:a]volume=1.0[a_voice];"
        f"[a_game][a_voice]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(start_sec),
        "-t", str(duration_sec),
        "-i", input_video,
        "-i", audio_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("\n[EROARE EXACTA FFMPEG]:\n", result.stderr)
        raise RuntimeError("Randarea FFmpeg a eșuat.")


# 5. EXECUȚIA PIPELINE-ULUI
async def main():
    n, requirements = ask_user()
    start_id, used_ranges = get_existing_clips()
    clips_data = analyze_gameplay(INPUT_VIDEO, n, requirements, used_ranges, start_id)


    whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")

    for clip in clips_data:
        cid = clip["id"]
        print(f"\n--- Procesare Clip #{cid}: {clip['title']} ({clip['start_time']} -> {clip['end_time']}) ---")

        audio_file = os.path.join(OUTPUT_DIR, f"voice_{cid}.mp3")
        ass_file = os.path.join(OUTPUT_DIR, f"subs_{cid}.ass")
        final_video = os.path.join(OUTPUT_DIR, f"short_{cid}.mp4")
        meta_file = os.path.join(OUTPUT_DIR, f"meta_{cid}.json")

        if not os.path.exists(audio_file) or os.path.getsize(audio_file) < 1000:
            print(" -> Generare voce TTS...")
            await generate_tts(clip["voiceover_script"], audio_file)

        if not os.path.exists(ass_file):
            print(" -> Generare subtitrări .ASS...")
            generate_ass_subtitles(audio_file, ass_file, whisper_model)

        print(" -> Randare video 9:16 în FFmpeg...")
        render_short(INPUT_VIDEO, clip["start_time"], clip["end_time"], audio_file, ass_file, final_video)

        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(clip, f, indent=2)

        print(f" [OK] Clip salvat cu succes în: {final_video}")


if __name__ == "__main__":
    asyncio.run(main())