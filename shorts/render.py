"""Randare FFmpeg 9:16: jump-cuts, fundal blur, zoom punch-in pe impact, grading, subtitrări, ducking + muzică, loudnorm."""
import glob
import os
import random
import subprocess

from . import config, media

FG_SIZE = 1080            # gameplay-ul se afișează pătrat 1080x1080 (crop central, mult mai mare decât 16:9)
FG_Y = 420                # poziția pe verticală (sub zona de titlu, deasupra zonei UI a platformei)
BASE_ZOOM = 0.05          # zoom lent pe durata clipului
PUNCH = 0.10              # intensitatea zoom-ului pe impact
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



def _zoom_expr(duration: float, pulses: list) -> str:
    terms = [f"{BASE_ZOOM}*t/{duration:.3f}"]
    for tp in pulses:
        terms.append(f"if(gte(t,{tp:.3f}),{PUNCH}*exp(-7*(t-{tp:.3f})),0)")
    return "1+" + "+".join(terms)


def _esc_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_short(src: str, segments: list, voice_wav: str, ass_path: str, out_path: str,
                 music: str = "", log=print) -> float:
    info = media.probe(src)
    voice_dur = media.audio_duration(voice_wav)
    segs = fit_segments(segments, voice_dur, info["duration"])
    durs = [b - a for a, b in segs]
    D = sum(durs)
    has_audio = _has_audio(src)

    # momente de impact: cut-uri + peak-uri audio
    pulses, cum = [], 0.0
    for (a, b), d in zip(segs, durs):
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
    for a, b in segs:
        cmd += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src]
    k = len(segs)
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
    f.append("[vb]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=5,"
             f"scale={config.OUT_W}:{config.OUT_H}:flags=bilinear,eq=brightness=-0.14:saturation=1.2[bg]")
    z = _zoom_expr(D, pulses)
    f.append(
        f"[vf]crop=ih:ih,scale=w='{FG_SIZE}*({z})':h='{FG_SIZE}*({z})':eval=frame:flags=bicubic,"
        f"crop={FG_SIZE}:{FG_SIZE},unsharp=5:5:0.9:5:5:0.0,eq=contrast=1.08:saturation=1.18:gamma=0.98,"
        f"vignette=PI/6:eval=init[fg]"
    )
    f.append(
        f"[bg][fg]overlay=(W-w)/2:{FG_Y}:shortest=1,"
        f"drawbox=x=0:y={FG_Y - 6}:w=iw:h=6:color=0xFFD400@0.9:t=fill,"
        f"drawbox=x=0:y={FG_Y + FG_SIZE}:w=iw:h=6:color=0xFFD400@0.9:t=fill,"
        f"ass='{_esc_path(ass_path)}',"
        f"drawbox=x=0:y={config.OUT_H - 16}:w='iw*t/{D:.3f}':h=16:color=0xFFD400@0.95:t=fill,"
        f"format=yuv420p[vout]"
    )

    # --- audio ---
    nkeys = 2 if music_idx is not None else 1
    keys = "".join(f"[vkey{i}]" for i in range(nkeys))
    f.append(f"[{voice_idx}:a]aresample=48000,aformat=channel_layouts=stereo,highpass=f=70,"
             f"acompressor=threshold=0.08:ratio=3:attack=5:release=80:makeup=2,volume=1.25,"
             f"asplit={nkeys + 1}{keys}[vmix]")
    f.append(f"[gaudio]volume={config.GAME_VOLUME}[g]")
    f.append("[g][vkey0]sidechaincompress=threshold=0.02:ratio=14:attack=10:release=450:makeup=1[gd]")
    mix_in = "[gd]"
    if music_idx is not None:
        f.append(f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={config.MUSIC_VOLUME},"
                 f"afade=t=in:d=1,afade=t=out:st={max(0.0, D - 2):.2f}:d=2,atrim=0:{D:.3f}[m]")
        f.append("[m][vkey1]sidechaincompress=threshold=0.02:ratio=8:attack=10:release=500:makeup=1[md]")
        mix_in += "[md]"
    n_in = 3 if music_idx is not None else 2
    f.append(f"{mix_in}[vmix]amix=inputs={n_in}:duration=first:normalize=0:dropout_transition=0,"
             f"loudnorm=I={config.TARGET_LUFS}:TP=-1.5:LRA=11,aresample=48000[aout]")

    cmd += ["-filter_complex", ";".join(f),
            "-map", "[vout]", "-map", "[aout]", "-t", f"{D:.3f}",
            "-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", str(config.CRF),
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(config.OUT_FPS),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Randarea FFmpeg a eșuat:\n" + r.stderr[-2500:])
    return D, segs
