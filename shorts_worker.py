#!/usr/bin/env python3
"""
Shorts Autopilot - Worker dung video (MK09).
Make chi DIEU PHOI; may nay lam viec nang: TTS (Piper), dung hinh (FFmpeg), phu de.

Tu lieu moi canh: "video" (mp4/webm) hoac "image". Dau ra: MP4 / H.264 / AAC / 1080x1920 / 30fps.

Dung:
  python shorts_worker.py job.json out.mp4
  python shorts_worker.py job.json out.mp4 --callback https://hook.eu2.make.com/XXXX

Bien moi truong (tuy chon):
  PIPER_BIN    duong dan toi `piper` (vd: /opt/piper/piper)
  PIPER_MODEL  duong dan toi model giong tieng Viet .onnx (hay NGHE THU truoc khi dung)
  SUB_FONT     ten font phu de (mac dinh: Arial)
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, urllib.request

W, H, FPS = 1080, 1920, 30
UA = {"User-Agent": "ShortsAutopilot/1.0 (personal research tool)"}


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Lenh loi: {' '.join(cmd[:3])}...\n{p.stderr[-1500:]}")
    return p.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", path]).strip())


def fetch(src, dst):
    """Tai anh (URL hoac duong dan). Khong co/loi -> bao loi, KHONG dung anh ngau nhien."""
    if not src:
        raise ValueError("Thieu tu lieu cho canh (NO_MATCH). Hay bo sung anh/video hop le.")
    if re.match(r"^https?://", src):
        req = urllib.request.Request(src, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
            shutil.copyfileobj(r, f)
    else:
        shutil.copy(src, dst)
    return dst


def make_voice(job, workdir):
    """Tao file giong doc. Uu tien: audio co san > Piper > bao loi (khong tu bia giong)."""
    if job.get("audio"):
        return job["audio"]
    piper, model = os.environ.get("PIPER_BIN"), os.environ.get("PIPER_MODEL")
    if not (piper and model):
        raise RuntimeError("Chua cau hinh Piper. Dat PIPER_BIN va PIPER_MODEL, "
                           "hoac them truong 'audio' (file wav/mp3) vao job.")
    out = os.path.join(workdir, "voice.wav")
    p = subprocess.run([piper, "-m", model, "-f", out],
                       input=job["voiceover"], text=True, capture_output=True)
    if p.returncode != 0 or not os.path.exists(out):
        raise RuntimeError("Piper loi: " + p.stderr[-800:])
    return out


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms//3600000:02}:{ms%3600000//60000:02}:{ms%60000//1000:02},{ms%1000:03}"


def build_srt(scenes, durs, path):
    t, lines = 0.0, []
    for i, (sc, d) in enumerate(zip(scenes, durs), 1):
        text = sc["text"].strip()
        # tach thanh cac dong ngan (~38 ky tu) de de doc tren man hinh doc
        words, cur, chunks = text.split(), "", []
        for w in words:
            if len(cur) + len(w) + 1 > 38:
                chunks.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur: chunks.append(cur)
        step = d / max(len(chunks), 1)
        for j, c in enumerate(chunks):
            a, b = t + j * step, t + (j + 1) * step
            lines.append(f"{len(lines)+1}\n{srt_time(a)} --> {srt_time(b)}\n{c}\n")
        t += d
    open(path, "w", encoding="utf-8").write("\n".join(lines))


VIDEO_EXT = (".mp4", ".mov", ".webm", ".m4v")


def is_video(src):
    return src.split("?")[0].lower().endswith(VIDEO_EXT)


def scenes_from_assets(job):
    """Payload tu Make: voiceover + asset_urls (chi gom tu lieu da TIM THAY).
    Chia cac cau cua loi doc deu cho tung tu lieu. Khong co tu lieu -> bao loi."""
    assets = [a for a in job.get("asset_urls", []) if a]
    if len(assets) < 2:
        raise ValueError(f"Chi co {len(assets)} tu lieu hop le (can >= 2). Khong dung hinh ngau nhien.")
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", job["voiceover"].strip()) if x.strip()]
    n = min(len(assets), len(sents))
    scenes, k = [], len(sents)
    for i in range(n):
        a, b = i * k // n, (i + 1) * k // n
        scenes.append({"text": " ".join(sents[a:b]), "image": assets[i]})
    return scenes


def render(job, out_path):
    scenes = job.get("scenes") or scenes_from_assets(job)
    if not scenes:
        raise ValueError("Job khong co canh nao.")
    with tempfile.TemporaryDirectory() as wd:
        voice = make_voice(job, wd)
        total = duration(voice)
        weights = [max(len(s["text"]), 1) for s in scenes]
        durs = [total * w / sum(weights) for w in weights]

        clips = []
        for i, (sc, d) in enumerate(zip(scenes, durs), 1):
            src = sc.get("video") or sc.get("image")
            is_video = bool(re.search(r"\.(mp4|webm|mov)(\?|$)", src or "", re.I))
            ext = ".mp4" if is_video else ".img"
            asset = fetch(src, os.path.join(wd, f"asset{i}{ext}"))
            clip = os.path.join(wd, f"clip{i}.mp4")
            frames = max(int(d * FPS), 1)
            bg = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H},boxblur=30:5[bg];"
                  f"[b]scale={W}:-2:force_original_aspect_ratio=decrease,"
                  f"scale='min({W},iw)':-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
            if is_video:   # video nen: lap lai neu ngan hon canh, cat theo thoi luong canh
                inp = ["-stream_loop", "-1", "-i", asset]
                vf = bg + f",fps={FPS},format=yuv420p"
            else:          # anh: zoom nhe (Ken Burns), can giua
                inp = ["-loop", "1", "-i", asset]
                vf = (bg + f",zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':"
                      f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p")
            run(["ffmpeg", "-y"] + inp + ["-t", f"{d:.3f}", "-vf", vf,
                 "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-an", clip])
            clips.append(clip)

        lst = os.path.join(wd, "list.txt")
        open(lst, "w").write("".join(f"file '{c}'\n" for c in clips))
        srt = os.path.join(wd, "sub.srt")
        build_srt(scenes, durs, srt)
        font = os.environ.get("SUB_FONT", "Arial")
        style = (f"FontName={font},FontSize=10,Bold=1,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H00000000,BorderStyle=1,Outline=1,Alignment=2,MarginV=60")
        srt_esc = srt.replace("\\", "/").replace(":", "\\:")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", voice,
             "-vf", f"subtitles='{srt_esc}':force_style='{style}'",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
             "-shortest", "-movflags", "+faststart", out_path])

    info = json.loads(run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                           "-show_format", out_path]))
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    return {"video_id": job.get("video_id"), "file": os.path.abspath(out_path),
            "container": "mp4", "video_codec": v["codec_name"], "audio_codec": a["codec_name"],
            "width": v["width"], "height": v["height"], "fps": v["r_frame_rate"],
            "duration_s": round(float(info["format"]["duration"]), 2),
            "size_mb": round(int(info["format"]["size"]) / 1e6, 2), "status": "REVIEW"}


def callback(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **UA})
    urllib.request.urlopen(req, timeout=30).read()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("job"); ap.add_argument("out"); ap.add_argument("--callback")
    a = ap.parse_args()
    job = json.load(open(a.job, encoding="utf-8"))
    try:
        result = render(job, a.out)
    except Exception as e:  # bao loi that, khong dung tu lieu/giong ngau nhien
        result = {"video_id": job.get("video_id"), "status": "FAILED", "error": str(e)[:1500]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if a.callback:
        try: callback(a.callback, result)
        except Exception as e: print("Callback loi:", e, file=sys.stderr)
    sys.exit(0 if result["status"] != "FAILED" else 1)
