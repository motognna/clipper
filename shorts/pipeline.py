"""Pipeline complet: analiză Gemini -> voce Kokoro -> subtitrări -> randare. Folosit de CLI și de GUI."""
import json
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor

from . import analyze, config, media, render, subs, tts


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
        log=print, progress=lambda done, total: None, stop=lambda: False) -> list:
    if not os.path.isfile(video):
        raise FileNotFoundError(f"Video inexistent: {video}")
    out_dir, work_dir = config.OUTPUT_DIR, config.WORK_DIR   # fixate pe durata rulării
    os.makedirs(work_dir, exist_ok=True)
    start_id, used = existing_clips(video)
    info = media.probe(video)
    log(f"Sursă: {os.path.basename(video)} – {info['width']}x{info['height']} @ {info['fps']:.0f}fps, "
        f"{info['duration'] / 60:.1f} min")
    log(f"Folder output: {out_dir}")

    moments = analyze.find_moments(video, n, requirements, used, game, log, stop)
    if not moments:
        raise RuntimeError("Gemini nu a returnat niciun moment valid. Încearcă din nou sau schimbă cerințele.")
    if len(moments) < n:
        log(f"Atenție: s-au găsit doar {len(moments)} din {n} momente.")
    total = len(moments)
    progress(0, total)
    key = media.video_key(video)

    done_paths, futures = [], []
    music_ok = use_music and bool(render.pick_music())
    if use_music and not music_ok:
        log("Nu există fișiere în folderul 'music/' – clipurile vor fi fără muzică.")

    def render_job(cid, clip, wav, ass):
        out = os.path.join(out_dir, f"short_{cid}.mp4")
        tmp = os.path.join(work_dir, f"short_{cid}.part.mp4")
        music = render.pick_music() if music_ok else ""
        dur, segs = render.render_short(video, clip["segments"], wav, ass, tmp, music, log)
        os.replace(tmp, out)
        clip = dict(clip, segments=[[round(a, 2), round(b, 2)] for a, b in segs])
        return cid, clip, out, dur

    def finish(result) -> str:
        cid, clip, out, dur = result
        clip = dict(clip, id=cid, source_key=key, duration_sec=round(dur, 1),
                    start_time=media.fmt_mmss(clip["segments"][0][0]),
                    end_time=media.fmt_mmss(clip["segments"][-1][1]))
        with open(os.path.join(out_dir, f"meta_{cid}.json"), "w", encoding="utf-8") as f:
            json.dump(clip, f, indent=2, ensure_ascii=False)
        _write_upload_text(os.path.join(out_dir, f"short_{cid}.txt"), clip)
        log(f"[OK] Clip #{cid} gata ({dur:.0f}s): {out}")
        return out

    with ThreadPoolExecutor(max_workers=config.RENDER_WORKERS) as pool:
        for i, clip in enumerate(moments):
            if stop():
                raise InterruptedError("Oprit de utilizator.")
            cid = start_id + i
            log(f"\n--- Clip #{cid}: {clip['title']} ---")
            wav = os.path.join(work_dir, f"voice_{cid}.wav")
            ass = os.path.join(work_dir, f"subs_{cid}.ass")
            log("Voce (Kokoro)...")
            vdur, spoken = tts.synthesize(clip["voiceover_script"], wav, voice)
            clip["voiceover_script"] = spoken          # scriptul final, cu call-to-action
            log("Subtitrări karaoke (Whisper)...")
            subs.build_ass(wav, clip["hook_text"], ass, vdur)
            log("Randare în fundal...")
            futures.append(pool.submit(render_job, cid, clip, wav, ass))

            for fut in [x for x in futures if x.done()]:   # colectează rezultatele terminate
                futures.remove(fut)
                done_paths.append(finish(fut.result()))
                progress(len(done_paths), total)

        for fut in futures:
            done_paths.append(finish(fut.result()))
            progress(len(done_paths), total)

    shutil.rmtree(work_dir, ignore_errors=True)
    return done_paths
