"""Configurare globală + încărcarea cheii API (din env / .env / api_key.txt)."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_ROOT = os.path.join(BASE_DIR, "output_shorts")   # rădăcina tuturor batch-urilor
OUTPUT_DIR = OUTPUT_ROOT                                # folderul curent de output (se poate schimba din GUI)
WORK_DIR = os.path.join(OUTPUT_DIR, "_work")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
MODELS_DIR = os.path.join(BASE_DIR, "models")
MUSIC_DIR = os.path.join(BASE_DIR, "music")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

DEFAULT_GAME = "GTA 6"
DEFAULT_VIDEO = os.path.join(BASE_DIR, "gameplay.mp4")

# --- Ieșire video ---
OUT_W, OUT_H = 1080, 1920
OUT_FPS = 60
CRF = 18
X264_PRESET = "fast"
RENDER_WORKERS = 2

# --- Voce (Kokoro, local) ---
VOICES = ["af_bella", "af_sarah", "af_nicole", "af_heart", "af_nova", "af_sky", "af_jessica"]
KOKORO_VOICE = "af_bella"      # cea mai expresivă dintre vocile feminine
HOOK_SPEED = 1.30              # prima propoziție (hook-ul) – agresivă și rapidă
BODY_SPEED = 1.18
CTA_SPEED = 1.12
EXCLAIM_BOOST = 0.07           # propozițiile cu "!" sunt rostite mai repede/energic
SENTENCE_GAP = 0.10            # pauză scurtă între propoziții (secunde)
VOICE_LEAD_IN = 0.25           # secunde de liniște înaintea vocii
CTA_VARIANTS = [
    "For more crazy content like this, drop a follow!",
    "Want more insane moments like this? Drop a follow!",
    "For more crazy content like this, please drop a follow!",
]

# --- Audio ---
TARGET_LUFS = -14
GAME_VOLUME = 0.55
MUSIC_VOLUME = 0.16

# --- Gemini ---
GEMINI_BATCH = 8               # momente cerute per apel (calitate mai bună pe loturi mici)
MIN_CLIP_SEC = 15
MAX_CLIP_SEC = 55
WORDS_PER_SEC = 3.1            # ritm de vorbire țintă (Kokoro la viteza de mai sus)

for _d in (OUTPUT_ROOT, CACHE_DIR, MUSIC_DIR):
    os.makedirs(_d, exist_ok=True)


def set_output_dir(path: str):
    """Schimbă folderul de output curent (creează și subfolderul de lucru)."""
    global OUTPUT_DIR, WORK_DIR
    OUTPUT_DIR = os.path.abspath(path)
    WORK_DIR = os.path.join(OUTPUT_DIR, "_work")
    os.makedirs(WORK_DIR, exist_ok=True)


set_output_dir(OUTPUT_ROOT)



def load_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    txt = os.path.join(BASE_DIR, "api_key.txt")
    if os.path.exists(txt):
        with open(txt, "r", encoding="utf-8") as f:
            val = f.read().strip()
            if val:
                return val
    raise ValueError(
        "Nu s-a găsit cheia Gemini API! Creează fișierul '.env' cu linia "
        "GEMINI_API_KEY=cheia_ta (sau setează variabila de mediu)."
    )
