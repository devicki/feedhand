"""Media checks and prep (ffmpeg/ffprobe). Limits are approximate; platforms change them."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

# Approximate per-channel limits (see README). Durations in seconds, sizes in bytes.
LIMITS = {
    "x": {"video_max_bytes": 512 * 1024**2, "video_max_s": 140, "max_w": 1920, "max_h": 1200, "max_fps": 60,
          "vcodec": {"h264"}, "acodec": {"aac"}, "containers": {".mp4", ".mov"},
          "gif_max_bytes": 15 * 1024**2, "image_max_bytes": 5 * 1024**2},
    "threads": {"video_max_bytes": 1024**3, "video_max_s": 300, "max_w": 1920, "max_h": 1920, "max_fps": 60,
                "vcodec": {"h264", "hevc"}, "acodec": {"aac"}, "containers": {".mp4", ".mov"},
                "gif_max_bytes": None, "image_max_bytes": 8 * 1024**2},
    "instagram": {"video_max_bytes": 300 * 1024**2, "video_max_s": 90, "max_w": 1920, "max_h": 1920,
                  "max_fps": 60, "vcodec": {"h264", "hevc"}, "acodec": {"aac"}, "containers": {".mp4", ".mov"},
                  "gif_max_bytes": None, "image_max_bytes": 8 * 1024**2},
}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def available() -> bool:
    return bool(shutil.which("ffprobe") and shutil.which("ffmpeg"))


def probe(path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        str(path)], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 0.0
    try:
        n, m = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").split("/")
        fps = float(n) / float(m) if float(m) else 0.0
    except ValueError:
        pass
    return {"duration": float(d.get("format", {}).get("duration") or 0), "size": Path(path).stat().st_size,
            "vcodec": v.get("codec_name"), "acodec": a.get("codec_name") if a else None,
            "width": v.get("width") or 0, "height": v.get("height") or 0, "fps": round(fps, 3)}


def check(path, channel="x") -> dict:
    """Return {'kind', 'info', 'issues': [...], 'needs_prep': bool, 'blocking': [...]}."""
    p = Path(path).expanduser()
    lim = LIMITS[channel]
    ext = p.suffix.lower()
    size = p.stat().st_size
    if ext == ".gif":
        issues = []
        if lim["gif_max_bytes"] and size > lim["gif_max_bytes"]:
            issues.append(f"GIF {size/1024**2:.1f}MB > {lim['gif_max_bytes']//1024**2}MB (convert to MP4)")
        return {"kind": "gif", "info": {"size": size}, "issues": issues, "needs_prep": bool(issues)}
    if ext not in VIDEO_EXT:
        issues = []
        if size > lim["image_max_bytes"]:
            issues.append(f"image {size/1024**2:.1f}MB > ~{lim['image_max_bytes']//1024**2}MB")
        return {"kind": "image", "info": {"size": size}, "issues": issues, "needs_prep": False,
                "note": "image resize not automated" if issues else None}
    info = probe(p)
    issues = []
    if ext not in lim["containers"]:
        issues.append(f"container {ext} -> mp4")
    if info["vcodec"] not in lim["vcodec"]:
        issues.append(f"video codec {info['vcodec']} -> h264")
    if info["acodec"] and info["acodec"] not in lim["acodec"]:
        issues.append(f"audio codec {info['acodec']} -> aac")
    if info["duration"] > lim["video_max_s"]:
        issues.append(f"duration {info['duration']:.1f}s > {lim['video_max_s']}s (will trim)")
    if info["width"] > lim["max_w"] or info["height"] > lim["max_h"]:
        issues.append(f"resolution {info['width']}x{info['height']} > {lim['max_w']}x{lim['max_h']}")
    if info["fps"] > lim["max_fps"]:
        issues.append(f"fps {info['fps']} > {lim['max_fps']}")
    if info["size"] > lim["video_max_bytes"]:
        issues.append(f"size {info['size']/1024**2:.0f}MB > {lim['video_max_bytes']//1024**2}MB")
    return {"kind": "video", "info": info, "issues": issues, "needs_prep": bool(issues)}


def _even(n):
    return n - n % 2


def prep(path, channel, out_dir, thumb=None) -> dict:
    p = Path(path).expanduser()
    out = Path(out_dir).expanduser() / "prepared"
    out.mkdir(parents=True, exist_ok=True)
    lim = LIMITS[channel]
    c = check(p, channel)
    res = {"input": str(p), "channel": channel, "kind": c["kind"], "issues": c["issues"], "output": str(p),
           "prepared": False, "trimmed": False}
    if c["needs_prep"]:
        dst = out / f"{p.stem}.{channel}.mp4"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(p)]
        vf = [f"scale='min({lim['max_w']},iw)':'min({lim['max_h']},ih)':force_original_aspect_ratio=decrease",
              "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        info = c["info"] if c["kind"] == "video" else {}
        if info.get("duration", 0) > lim["video_max_s"]:
            cmd += ["-t", str(lim["video_max_s"] - 0.5)]
            res["trimmed"] = True
        if info.get("fps", 0) > lim["max_fps"]:
            cmd += ["-r", str(lim["max_fps"])]
        cmd += ["-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]
        subprocess.run(cmd, check=True, capture_output=True)
        res.update(output=str(dst), prepared=True)
        after = check(dst, channel)
        res["remaining_issues"] = after["issues"]
    if thumb is not None:
        tp = out / f"{p.stem}.thumb.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(thumb), "-i", res["output"], "-frames:v", "1",
                        "-q:v", "2", str(tp)], check=True, capture_output=True)
        res["thumbnail"] = str(tp)
    return res
