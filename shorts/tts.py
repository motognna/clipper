"""Voiceover local cu Kokoro TTS (ONNX, rulează pe CPU, fără API).

Fiecare propoziție se sintetizează separat, cu viteză proprie: hook-ul (prima propoziție) e rostit agresiv și rapid,
propozițiile cu '!' sunt mai energice, iar la final se adaugă automat call-to-action-ul ("drop a follow").
"""
import os
import random
import re

import numpy as np
import soundfile as sf

from . import config

_kokoro = None


def _get_engine():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        model = os.path.join(config.MODELS_DIR, "kokoro-v1.0.onnx")
        voices = os.path.join(config.MODELS_DIR, "voices-v1.0.bin")
        if not (os.path.exists(model) and os.path.exists(voices)):
            raise FileNotFoundError(
                "Lipsesc fișierele Kokoro din 'models/'. Descarcă kokoro-v1.0.onnx și voices-v1.0.bin "
                "de la github.com/thewh1teagle/kokoro-onnx/releases (model-files-v1.0)."
            )
        _kokoro = Kokoro(model, voices)
    return _kokoro


def clean_text(text: str) -> str:
    t = re.sub(r"[*_#<>~`\[\]{}|\\]", "", str(text))
    t = t.replace("&", " and ").replace("…", "...").replace("—", ", ").replace("–", ", ")
    t = re.sub(r"\.{2,}", ".", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or "Watch this insane moment!"


def split_sentences(text: str) -> list:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", clean_text(text)) if p.strip()]
    merged = []
    for p in parts:  # propozițiile foarte scurte se lipesc de următoarea (ritm mai natural)
        if merged and len(merged[-1].split()) < 3 and not re.search(r"[!?]$", merged[-1]):
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


def _trim(x: np.ndarray, sr: int, tail: float = 0.06) -> np.ndarray:
    nz = np.flatnonzero(np.abs(x) > 0.012)
    if not len(nz):
        return x
    return x[max(0, nz[0] - int(0.02 * sr)): nz[-1] + int(tail * sr)]


def synthesize(text: str, out_wav: str, voice: str = config.KOKORO_VOICE, add_cta: bool = True) -> tuple:
    """Generează vocea completă. Întoarce (durata în secunde, textul complet rostit)."""
    kokoro = _get_engine()
    sentences = split_sentences(text)
    if add_cta:
        sentences.append(random.choice(config.CTA_VARIANTS))

    chunks, sr = [], 24000
    for i, s in enumerate(sentences):
        is_cta = add_cta and i == len(sentences) - 1
        if is_cta:
            speed = config.CTA_SPEED
        elif i == 0:
            speed = config.HOOK_SPEED
        else:
            speed = config.BODY_SPEED
        if s.endswith("!") and not is_cta:
            speed += config.EXCLAIM_BOOST
        samples, sr = kokoro.create(s, voice=voice, speed=speed, lang="en-us")
        samples = _trim(np.asarray(samples, dtype=np.float32), sr)
        peak = float(np.max(np.abs(samples))) or 1.0
        samples = samples / peak * 0.9            # nivel constant pe propoziție
        if i == 0:
            samples = np.clip(samples * 1.12, -0.98, 0.98)   # hook-ul puțin mai tare
        chunks.append(samples)
        if i < len(sentences) - 1:
            chunks.append(np.zeros(int(config.SENTENCE_GAP * sr), dtype=np.float32))

    lead = np.zeros(int(config.VOICE_LEAD_IN * sr), dtype=np.float32)
    audio = np.concatenate([lead] + chunks + [np.zeros(int(0.15 * sr), dtype=np.float32)])
    sf.write(out_wav, audio, sr)
    return len(audio) / sr, " ".join(sentences)
