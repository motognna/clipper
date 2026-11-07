"""Utilitare ffmpeg/ffprobe: informații video, proxy mic pentru Gemini, detectare peak-uri audio."""
import hashlib
import json
import os
import subprocess

import numpy as np

from . import config


def parse_seconds(ts) -> float:
    parts = [float(p) for p in str(ts).strip().split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def fmt_mmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    st = data["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return {
        "width": int(st["width"]),
        "height": int(st["height"]),
        "fps": float(num) / float(den or 1),
        "duration": float(data["format"]["duration"]),
    }


def audio_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def video_key(path: str) -> str:
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def make_proxy(video_path: str, log=print) -> str:
    """Copie 480p mică, ultra-rapidă, cu timecode vizual imprimat pentru analiza de precizie cadru cu cadru a Gemini."""
    proxy = os.path.join(config.CACHE_DIR, f"proxy_{video_key(video_path)}.mp4")
    if os.path.exists(proxy) and os.path.getsize(proxy) > 1000:
        return proxy
    log("Se creează proxy-ul 480p optimizat (cu timecode vizual pentru precizie)...")
    tmp = proxy + ".part.mp4"
    # fps=2 este ideal pentru Gemini File API (reduce dimensiunea la 1/5 fără a pierde scene),
    # iar timecode-ul imprimat în colț permite identificarea milimetrică a cadrelor de către AI.
    vf = "scale=-2:480,fps=2,drawtext=text='%{pts\\:hms}':x=16:y=16:fontcolor=white:box=1:boxcolor=black@0.85:fontsize=26"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-threads", "0", "-i", video_path,
           "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
           "-c:a", "aac", "-b:a", "48k", "-ac", "1", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Crearea proxy-ului a eșuat:\n" + r.stderr)
    os.replace(tmp, proxy)
    return proxy


def snap_to_beat(video_path: str, t: float, window: float = 0.8) -> float:
    """Ajustează un timestamp către cel mai apropiat beat audio/impact dacă există unul în fereastră."""
    start = max(0.0, t - window)
    dur = window * 2
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
           "-i", video_path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or len(r.stdout) < 800:
        return t
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
    win = 400  # 0.05s
    n = len(x) // win
    if n < 4:
        return t
    energy = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1))
    rise = energy - np.concatenate([[energy[0]], energy[:-1]])
    best_idx = int(np.argmax(rise))
    best_t = start + (best_idx * 0.05)
    # Dacă creșterea de energie este semnificativă, snap către beat
    if rise[best_idx] > np.median(energy) * 1.8:
        return round(best_t, 2)
    return t


def audio_peaks(video_path: str, start: float, end: float, max_peaks: int = 3,
                min_gap: float = 3.0) -> list:
    """Momentele (în secunde, relative la start) cu cea mai mare energie audio – folosite pentru zoom pe impact."""
    dur = max(0.1, end - start)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
           "-i", video_path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or len(r.stdout) < 1600:
        return []
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
    win = 800  # 0.1s
    n = len(x) // win
    if n < 5:
        return []
    energy = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1))
    # creșterea bruscă de energie = impact/explozie
    rise = energy - np.concatenate([[energy[0]], energy[:-1]])
    score = energy * 0.5 + np.clip(rise, 0, None) * 2.0
    peaks = []
    for idx in np.argsort(score)[::-1]:
        t = idx * 0.1
        if t < 1.0 or t > dur - 0.8:
            continue
        if all(abs(t - p) >= min_gap for p in peaks):
            peaks.append(float(t))
        if len(peaks) >= max_peaks:
            break
    return sorted(peaks)
