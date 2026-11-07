"""Randare FFmpeg 9:16: jump-cuts, fundal blur, zoom punch-in pe impact, grading, subtitrări, ducking + muzică, loudnorm."""
import glob
import os
import random
import subprocess

from . import config, media, sfx, styles

FG_SIZE = 1080            # gameplay-ul se afișează pătrat 1080x1080 (crop central, mult mai mare decât 16:9)
FG_Y = 420                # poziția pe verticală (sub zona de titlu, deasupra zonei UI a platformei)
MAX_PUNCHES = 6


def pick_music() -> str:
    exts = ("mp3", "wav", "m4a", "ogg", "flac", "aac")
    files = []
    for e in exts:
        files += glob.glob(os.path.join(config.MUSIC_DIR, f"*.{e}"))
    return random.choice(files) if files else ""


def _has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=codec_type", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return "audio" in out


def fit_segments(segments: list, voice_dur: float, video_dur: float) -> list:
    """Potrivește durata clipului cu vocea: clipul se termină la ~0.5s după ultima silabă (CTA-ul),
    deci nu rămâne niciodată o coadă fără voiceover. Dacă vocea e mai lungă, se extinde ultimul segment."""
    segs = [list(s) for s in segments]
    target = voice_dur + 0.5
    total = sum(b - a for a, b in segs)
    if total < target:          # extinde ultimul segment (apoi înapoi, dacă s-a ajuns la finalul sursei)
        a, b = segs[-1]
        grow = target - total
        b2 = min(video_dur, b + grow)
        grow -= b2 - b
        a2 = max(0.0, a - grow) if grow > 0 else a
        segs[-1] = [a2, b2]
    elif total > target:        # taie surplusul de la final
        excess = total - target
        while excess > 0.01:
            a, b = segs[-1]
            if b - a - excess >= 2.0 or len(segs) == 1:
                segs[-1] = [a, max(a + 2.0, b - excess)]
                break
            excess -= b - a      # segmentul întreg iese din montaj
            segs.pop()
            if excess < 0:       # am tăiat prea mult -> prelungim noul ultim segment
                segs[-1][1] = min(video_dur, segs[-1][1] - excess)
                break
    return segs



def _zoom_expr(duration: float, pulses: list, base: float, punch: float) -> str:
    terms = [f"{base}*t/{duration:.3f}"]
    for tp in pulses:
        terms.append(f"if(gte(t,{tp:.3f}),{punch}*exp(-7*(t-{tp:.3f})),0)")
    return "1+" + "+".join(terms)


def _esc_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_short(src: str, segments: list, voice_wav: str, ass_path: str, out_path: str,
                 music: str = "", log=print, style: dict = None,
                 teaser_seg: list = None, climax_time: float = None) -> tuple:
    style = style or styles.STYLES[0]
    info = media.probe(src)
    voice_dur = media.audio_duration(voice_wav)

    # Dacă avem teaser la început, durata poveștii principale este adaptată corespunzător
    teaser_dur = round(teaser_seg[1] - teaser_seg[0], 3) if teaser_seg else 0.0
    story_voice_dur = max(10.0, voice_dur - teaser_dur) if teaser_dur > 0 else voice_dur
    fit_segs = fit_segments(segments, story_voice_dur, info["duration"])

    all_segs = ([teaser_seg] if teaser_seg else []) + fit_segs
    durs = [b - a for a, b in all_segs]
    D = sum(durs)
    has_audio = _has_audio(src)

    # momente de impact: cut-uri + peak-uri audio
    pulses, cum = [], 0.0
    for (a, b), d in zip(all_segs, durs):
        if cum > 0:
            pulses.append(cum + 0.02)
        pulses += [cum + p for p in media.audio_peaks(src, a, b, max_peaks=2)]
        cum += d
    pulses = sorted(pulses)
    pruned = []
    for p in pulses:
        if not pruned or p - pruned[-1] >= 1.5:
            pruned.append(p)
    pulses = pruned[:MAX_PUNCHES]

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for a, b in all_segs:
        cmd += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src]
    k = len(all_segs)
    cmd += ["-i", voice_wav]
    voice_idx = k
    music_idx = None
    if music:
        mdur = 0.0
        try:
            mdur = media.audio_duration(music)
        except Exception:
            pass
        off = random.uniform(0, max(0.0, mdur - D - 5)) if mdur > D + 10 else 0.0
        cmd += ["-stream_loop", "-1", "-ss", f"{off:.2f}", "-t", f"{D + 1:.2f}", "-i", music]
        music_idx = k + 1

    # efecte sonore: pop-uri multiple (hook, mid, climax), whoosh la fiecare cut, și teaser stinger
    whooshes, c = [], 0.0
    for d in durs[:-1]:
        c += d
        whooshes.append(c - 0.05)

    hook_pop_at = teaser_dur + 0.05
    mid_pop_at = teaser_dur + (D - teaser_dur) * 0.42 if D > 14.0 else 0.0
    climax_pop_at = max(teaser_dur + 8.0, D - 4.5) if D > 16.0 else 0.0

    extra_pops = []
    if mid_pop_at > hook_pop_at + 2.0:
        extra_pops.append((mid_pop_at, "mid"))
    if climax_pop_at > mid_pop_at + 2.0:
        extra_pops.append((climax_pop_at, "climax"))

    sfx_wav = os.path.splitext(out_path)[0] + "_sfx.wav"
    sfx.make_track(sfx_wav, D, pop_at=hook_pop_at, whoosh_at=whooshes,
                   teaser_dur=teaser_dur, climax_at=climax_pop_at, extra_pops=extra_pops)
    sfx_idx = (music_idx if music_idx is not None else k) + 1
    cmd += ["-i", sfx_wav]

    f = []
    # --- concat segmente (jump-cuts) ---
    parts = []
    for i in range(k):
        f.append(f"[{i}:v]fps={config.OUT_FPS},setsar=1,format=yuv420p[cv{i}]")
        if has_audio:
            f.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[ca{i}]")
        else:
            f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{durs[i]:.3f}[ca{i}]")
        parts.append(f"[cv{i}][ca{i}]")
    f.append("".join(parts) + f"concat=n={k}:v=1:a=1[vcat][gaudio]")

    # --- video ---
    f.append("[vcat]split=2[vb][vf]")
    f.append("[vb]scale=128:228:force_original_aspect_ratio=increase,crop=128:228,boxblur=luma_radius=6:luma_power=2,"
             f"scale={config.OUT_W}:{config.OUT_H}:flags=bilinear,eq=brightness=-0.14:saturation=1.2[bg]")
    z = _zoom_expr(D, pulses, style["zoom"], style["punch"])
    f.append(
        f"[vf]crop=ih:ih,scale=w='{FG_SIZE}*({z})':h='{FG_SIZE}*({z})':eval=frame:flags=bilinear,"
        f"crop={FG_SIZE}:{FG_SIZE},unsharp=3:3:0.8:3:3:0.0,eq=contrast=1.08:saturation=1.18:gamma=0.98,"
        f"vignette=PI/6:eval=init[fg]"
    )
    acc = style["accent_hex"]
    f.append(
        f"[bg][fg]overlay=(W-w)/2:{FG_Y}:shortest=1,"
        f"drawbox=x=0:y={FG_Y - 6}:w=iw:h=6:color={acc}@0.9:t=fill,"
        f"drawbox=x=0:y={FG_Y + FG_SIZE}:w=iw:h=6:color={acc}@0.9:t=fill,"
        f"ass='{_esc_path(ass_path)}',"
        f"drawbox=x=0:y={config.OUT_H - 16}:w='iw*t/{D:.3f}':h=16:color={acc}@0.95:t=fill,"
        f"format=yuv420p[vout]"
    )

    # --- audio ---
    nkeys = 2 if music_idx is not None else 1
    keys = "".join(f"[vkey{i}]" for i in range(nkeys))
    # Curba de egalizare stil Shure SM7B: filtru trece-sus 75Hz, căldură 250Hz, claritate/prezență la 3.4kHz
    f.append(f"[{voice_idx}:a]aresample=48000,aformat=channel_layouts=stereo,highpass=f=75,"
             f"equalizer=f=250:width_type=o:w=1.0:g=1.5,equalizer=f=3400:width_type=o:w=1.2:g=2.8,"
             f"acompressor=threshold=0.07:ratio=3.5:attack=4:release=75:makeup=2.2,volume=1.22,"
             f"asplit={nkeys + 1}{keys}[vmix]")
    f.append(f"[gaudio]volume={config.GAME_VOLUME}[g]")
    f.append("[g][vkey0]sidechaincompress=threshold=0.02:ratio=14:attack=10:release=450:makeup=1[gd]")
    mix_in = "[gd]"
    if music_idx is not None:
        f.append(f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={config.MUSIC_VOLUME},"
                 f"afade=t=in:d=1,afade=t=out:st={max(0.0, D - 2):.2f}:d=2,atrim=0:{D:.3f}[m]")
        f.append("[m][vkey1]sidechaincompress=threshold=0.02:ratio=8:attack=10:release=500:makeup=1[md]")
        mix_in += "[md]"
    f.append(f"[{sfx_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.60[sx]")
    mix_in += "[sx]"
    n_in = 4 if music_idx is not None else 3
    f.append(f"{mix_in}[vmix]amix=inputs={n_in}:duration=first:normalize=0:dropout_transition=0,"
             f"loudnorm=I={config.TARGET_LUFS}:TP=-1.5:LRA=11,aresample=48000[aout]")

    cmd += ["-filter_complex", ";".join(f),
            "-map", "[vout]", "-map", "[aout]", "-t", f"{D:.3f}",
            "-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", str(config.CRF), "-threads", "0",
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(config.OUT_FPS),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]

    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        os.remove(sfx_wav)
    except Exception:
        pass
    if r.returncode != 0:
        raise RuntimeError("Randarea FFmpeg a eșuat:\n" + r.stderr[-2500:])
    return D, all_segs
