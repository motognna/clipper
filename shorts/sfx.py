"""Efecte sonore generate procedural (originale, fără drepturi de autor): 'pop' la hook, 'whoosh' la cut-uri,
stinger pentru climax teaser și boom la momentul culminant."""
import numpy as np
import soundfile as sf

SR = 48000


def _pop() -> np.ndarray:
    t = np.arange(int(SR * 0.28)) / SR
    freq = 520 * np.exp(-t * 14) + 90            # sweep descendent = „pop"
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 11)
    click = np.random.default_rng(1).standard_normal(len(t)) * np.exp(-t * 90) * 0.35
    return (body + click).astype(np.float32)


def _pop_high() -> np.ndarray:
    t = np.arange(int(SR * 0.22)) / SR
    freq = 680 * np.exp(-t * 16) + 120           # pop mai înalt, perfect pentru mid-tension
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 14)
    click = np.random.default_rng(3).standard_normal(len(t)) * np.exp(-t * 110) * 0.30
    return (body + click).astype(np.float32)


def _whoosh() -> np.ndarray:
    n = int(SR * 0.30)
    t = np.arange(n) / SR
    noise = np.random.default_rng(2).standard_normal(n)
    env = np.sin(np.pi * np.clip(t / 0.30, 0, 1)) ** 2
    # filtru trece-jos care se deschide în timp (zgomot „aerisit")
    out = np.zeros(n)
    alpha = 0.05 + 0.5 * (t / 0.30)
    acc = 0.0
    for i in range(n):
        acc += alpha[i] * (noise[i] - acc)
        out[i] = acc
    return (out * env * 1.6).astype(np.float32)


def _climax_boom() -> np.ndarray:
    """Impact puternic de bas (sub-bass drop + snap) pentru momentul culminant."""
    n = int(SR * 0.50)
    t = np.arange(n) / SR
    freq = 140 * np.exp(-t * 12) + 40
    sub = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 6.5)
    noise = np.random.default_rng(4).standard_normal(n) * np.exp(-t * 40) * 0.4
    return (sub + noise).astype(np.float32)


def _teaser_stinger(dur: float = 1.2) -> np.ndarray:
    """Riser de tensiune + reverse whoosh pentru preview-ul de la secunda 0."""
    dur = max(0.4, dur)
    n = int(SR * dur)
    t = np.arange(n) / SR
    # Riser de frecvență ascendentă
    freq = 100 + 750 * ((t / dur) ** 2.2)
    tone = np.sin(2 * np.pi * np.cumsum(freq) / SR) * ((t / dur) ** 1.8) * 0.6
    # Reverse whoosh
    noise = np.random.default_rng(5).standard_normal(n)
    env = ((t / dur) ** 2.5) * 0.8
    snd = tone + noise * env
    # Fade in la început, tape-stop scurt chiar înainte de cut
    ramp_in = min(n // 8, int(SR * 0.08))
    snd[:ramp_in] *= np.linspace(0.0, 1.0, ramp_in)
    cut_tail = min(n // 6, int(SR * 0.04))
    snd[-cut_tail:] *= np.linspace(1.0, 0.0, cut_tail)
    return snd.astype(np.float32)


def make_track(path: str, duration: float, pop_at=0.05, whoosh_at: list = None,
               teaser_dur: float = 0.0, climax_at: float = 0.0, extra_pops: list = None):
    """Scrie un wav stereo de lungimea clipului cu efectele plasate la momentele cerute."""
    whoosh_at = whoosh_at or []
    extra_pops = extra_pops or []
    n = int(SR * (duration + 0.5))
    track = np.zeros(n, dtype=np.float32)

    # 1. Teaser stinger la secunda 0
    if teaser_dur > 0.2:
        stg = _teaser_stinger(teaser_dur)
        track[: min(n, len(stg))] += stg[: min(n, len(stg))]

    # 2. Pop-uri (hook, mid, extra)
    pop_list = []
    if isinstance(pop_at, (list, tuple)):
        pop_list.extend(pop_at)
    elif pop_at is not None:
        pop_list.append((float(pop_at), "hook"))

    for item in extra_pops:
        if isinstance(item, (int, float)):
            pop_list.append((float(item), "mid"))
        elif isinstance(item, tuple) and len(item) == 2:
            pop_list.append(item)

    for item in pop_list:
        t, kind = (item if isinstance(item, tuple) else (float(item), "hook"))
        snd = _pop_high() if kind == "mid" else _pop()
        i = int(max(0.0, t) * SR)
        seg = snd[: max(0, n - i)]
        track[i:i + len(seg)] += seg

    # 3. Cut whooshes
    for w in whoosh_at:
        snd = _whoosh()
        i = int(max(0.0, w) * SR)
        seg = snd[: max(0, n - i)]
        track[i:i + len(seg)] += seg

    # 4. Climax boom
    if climax_at > 0.5:
        snd = _climax_boom()
        i = int(max(0.0, climax_at) * SR)
        seg = snd[: max(0, n - i)]
        track[i:i + len(seg)] += seg

    peak = float(np.max(np.abs(track))) or 1.0
    track = track / peak * 0.8
    sf.write(path, np.stack([track, track], axis=1), SR)

