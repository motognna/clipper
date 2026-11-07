"""Pipeline complet: analiză Gemini -> voce Kokoro -> subtitrări -> randare. Folosit de CLI și de GUI."""
import json
import os
import random
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import analyze, config, media, render, styles, subs, tts


def existing_clips(video: str = "") -> tuple:
    """(următorul id liber în folderul curent, intervalele deja folosite din TOATE folderele de output,
    doar pentru același video sursă – ca un folder nou să nu repete momente deja tăiate)."""
    max_id = 0
    for name in os.listdir(config.OUTPUT_DIR):
        m = re.fullmatch(r"(?:meta|short)_(\d+)\.(?:json|mp4)", name)
        if m:
            max_id = max(max_id, int(m.group(1)))

    key = media.video_key(video) if video else ""
    is_legacy_video = os.path.basename(video) == "gameplay.mp4"
    used = []
    for root, dirs, files in os.walk(config.OUTPUT_ROOT):
        dirs[:] = [d for d in dirs if d != "_work"]
        for name in files:
            if not re.fullmatch(r"meta_\d+\.json", name):
                continue
            try:
                with open(os.path.join(root, name), "r", encoding="utf-8") as f:
                    c = json.load(f)
                src = c.get("source_key")
                if src is not None and src != key:
                    continue
                if src is None and not is_legacy_video:   # meta vechi (fără sursă) = gameplay.mp4
                    continue
                if "segments" in c:
                    used.append(f"{media.fmt_mmss(c['segments'][0][0])}-{media.fmt_mmss(c['segments'][-1][1])}")
                else:
                    used.append(f"{c['start_time']}-{c['end_time']}")
            except Exception:
                pass
    return max_id + 1, used



def _write_upload_text(path: str, clip: dict):
    tags = " ".join("#" + t for t in clip.get("tags", []))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{clip['title']}\n\n{clip.get('description', '')}\n\n{tags}\n")


def run(video: str, n: int, requirements: str = "", game: str = config.DEFAULT_GAME,
        use_music: bool = True, voice: str = config.KOKORO_VOICE,
        log=print, progress=lambda done, total: None, stop=lambda: False, warn=None,
        context: str = "", use_teaser: bool = True) -> list:
    if not os.path.isfile(video):
        raise FileNotFoundError(f"Video inexistent: {video}")
    out_dir, work_dir = config.OUTPUT_DIR, config.WORK_DIR   # fixate pe durata rulării
    os.makedirs(work_dir, exist_ok=True)
    start_id, used = existing_clips(video)
    info = media.probe(video)
    log(f"Sursă: {os.path.basename(video)} – {info['width']}x{info['height']} @ {info['fps']:.0f}fps, "
        f"{info['duration'] / 60:.1f} min")
    log(f"Folder output: {out_dir}")

    moments = analyze.find_moments(video, n, requirements, used, game, log, stop, warn, context=context)
    if not moments:
        raise RuntimeError("Gemini nu a returnat niciun moment valid. Încearcă din nou sau schimbă cerințele.")
    if len(moments) < n:
        log(f"Atenție: s-au găsit doar {len(moments)} din {n} momente.")
    total = len(moments)
    progress(0, total)
    key = media.video_key(video)

    done_paths = []
    music_ok = use_music and bool(render.pick_music())
    if use_music and not music_ok:
        log("Nu există fișiere în folderul 'music/' – clipurile vor fi fără muzică.")

    def process_clip_task(cid, clip, clip_style, pop_text, mid_pop, climax_pop, teaser_seg, teaser_dur, climax_t):
        if stop():
            raise InterruptedError("Oprit de utilizator.")

        wav = os.path.join(work_dir, f"voice_{cid}.wav")
        ass = os.path.join(work_dir, f"subs_{cid}.ass")
        out = os.path.join(out_dir, f"short_{cid}.mp4")
        tmp = os.path.join(work_dir, f"short_{cid}.part.mp4")

        log(f"[{cid}] Voce (Kokoro TTS)...")
        lead_in = (teaser_dur + 0.15) if teaser_dur > 0 else None
        vdur, spoken = tts.synthesize(clip["voiceover_script"], wav, voice, lead_in=lead_in)
        clip_copy = dict(clip, voiceover_script=spoken)

        if stop():
            raise InterruptedError("Oprit de utilizator.")

        log(f"[{cid}] Subtitrări karaoke & efecte pop (Whisper)...")
        subs.build_ass(wav, clip_copy["hook_text"], ass, vdur, style=clip_style, pop_text=pop_text,
                       mid_pop=mid_pop, climax_pop=climax_pop,
                       teaser_active=bool(teaser_seg), teaser_dur=teaser_dur)

        if stop():
            raise InterruptedError("Oprit de utilizator.")

        log(f"[{cid}] Randare video FFmpeg...")
        music = render.pick_music() if music_ok else ""
        dur, segs = render.render_short(video, clip_copy["segments"], wav, ass, tmp, music, log,
                                        style=clip_style, teaser_seg=teaser_seg, climax_time=climax_t)
        os.replace(tmp, out)
        clip_copy = dict(clip_copy, segments=[[round(a, 2), round(b, 2)] for a, b in segs])
        return cid, clip_copy, out, dur

    def finish(result) -> str:
        cid, clip, out, dur = result
        clip = dict(clip, id=cid, source_key=key, duration_sec=round(dur, 1),
                    start_time=media.fmt_mmss(clip["segments"][0][0]),
                    end_time=media.fmt_mmss(clip["segments"][-1][1]))
        with open(os.path.join(out_dir, f"meta_{cid}.json"), "w", encoding="utf-8") as f:
            json.dump(clip, f, indent=2, ensure_ascii=False)
        _write_upload_text(os.path.join(out_dir, f"short_{cid}.txt"), clip)
        log(f"[OK] Clip #{cid} finalizat ({dur:.0f}s): {out}")
        return out

    with ThreadPoolExecutor(max_workers=config.RENDER_WORKERS) as pool:
        future_to_cid = {}
        for i, clip in enumerate(moments):
            if stop():
                raise InterruptedError("Oprit de utilizator.")
            cid = start_id + i
            clip_style = styles.style_for(cid)
            pop_text = clip.get("pop_text") or random.choice(styles.HOOK_POPS)
            mid_pop = clip.get("mid_pop") or random.choice(styles.MID_POPS)
            climax_pop = clip.get("climax_pop") or random.choice(styles.CLIMAX_POPS)
            climax_t = clip.get("climax_time")

            # Determinare segment teaser (climax preview la secunda 0)
            teaser_seg = None
            teaser_dur = 0.0
            if use_teaser and clip.get("segments"):
                if climax_t and climax_t > 0:
                    t_start = max(0.0, climax_t - 0.2)
                    teaser_seg = [t_start, min(info["duration"], t_start + 1.2)]
                else:
                    last_s = clip["segments"][-1]
                    t_start = max(last_s[0], last_s[1] - 1.5)
                    teaser_seg = [t_start, min(info["duration"], t_start + 1.2)]
                teaser_dur = round(teaser_seg[1] - teaser_seg[0], 2)

            clip["style"] = clip_style["name"]
            clip["pop_text"] = pop_text
            clip["mid_pop"] = mid_pop
            clip["climax_pop"] = climax_pop
            clip["teaser_active"] = bool(teaser_seg)
            clip["teaser_dur"] = teaser_dur

            log(f"\n--- Clip #{cid}: {clip['title']} (stil: {clip_style['name']}"
                + (", cu teaser climax" if teaser_seg else "") + ") ---")

            fut = pool.submit(process_clip_task, cid, clip, clip_style, pop_text, mid_pop, climax_pop,
                              teaser_seg, teaser_dur, climax_t)
            future_to_cid[fut] = cid

        for fut in as_completed(future_to_cid):
            if stop():
                for f in future_to_cid:
                    f.cancel()
                raise InterruptedError("Oprit de utilizator.")
            res = fut.result()
            done_paths.append(finish(res))
            progress(len(done_paths), total)

    done_paths.sort()
    shutil.rmtree(work_dir, ignore_errors=True)
    return done_paths
