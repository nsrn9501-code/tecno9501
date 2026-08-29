import os
import re
import shutil
import subprocess

from yt_dlp import YoutubeDL

from .config import DOWNLOAD_DIR, COOKIES_DIR, MAX_FILE_SIZE


PLATFORM_PATTERNS = [
    ("youtube", r"(youtube\.com|youtu\.be)"),
    ("instagram", r"instagram\.com"),
    ("tiktok", r"tiktok\.com"),
    ("facebook", r"(facebook\.com|fb\.watch)"),
]


def detect_platform(url):
    for name, pat in PLATFORM_PATTERNS:
        if re.search(pat, url):
            return name
    return None


def _cookie_file(platform):
    p = os.path.join(COOKIES_DIR, f"{platform}.txt")
    return p if os.path.exists(p) else None


def _base_opts(platform, outtmpl):
    opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 20,
    }
    cookie = _cookie_file(platform)
    if cookie:
        opts["cookiefile"] = cookie
    return opts


def search_youtube(query, limit=5):
    ydl = YoutubeDL(_base_opts("youtube", "%(title)s.%(ext)s"))
    try:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        results = []
        for e in info.get("entries", []):
            results.append({
                "id": e.get("id"),
                "title": e.get("title"),
                "duration": e.get("duration"),
                "uploader": e.get("channel") or e.get("uploader"),
            })
        return results
    except Exception as exc:
        raise DownloadError(f"بحث فشل: {exc}")


def fetch_video_info(url):
    platform = detect_platform(url)
    if not platform:
        raise DownloadError("الرابط غير مدعوم.")
    ydl = YoutubeDL(_base_opts(platform, "%(title)s.%(ext)s"))
    try:
        info = ydl.extract_info(url, download=False)
        title = info.get("title") or "فيديو"
        return {"title": title, "platform": platform}
    except Exception as exc:
        raise DownloadError(f"تعذر الوصول للرابط: {exc}")


def download_audio(url):
    platform = detect_platform(url)
    if not platform:
        raise DownloadError("الرابط غير مدعوم.")
    outtmpl = os.path.join(DOWNLOAD_DIR, f"audio_{os.getpid()}_%(id)s.%(ext)s")
    opts = _base_opts(platform, outtmpl)
    opts.update({
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    })
    return _run(platform, url, opts, "audio")


def download_video(url, max_height=1080):
    platform = detect_platform(url)
    if not platform:
        raise DownloadError("الرابط غير مدعوم.")
    outtmpl = os.path.join(DOWNLOAD_DIR, f"video_{os.getpid()}_%(id)s.%(ext)s")
    opts = _base_opts(platform, outtmpl)
    fmt = f"bestvideo[ext=mp4][height<=?{max_height}]+bestaudio[ext=m4a]/bestvideo[height<=?{max_height}]+bestaudio/best[ext=mp4]/best"
    opts.update({
        "format": fmt,
        "merge_output_format": "mp4",
    })
    try:
        return _run(platform, url, opts, "video")
    except DownloadError:
        if platform in ("instagram", "tiktok", "facebook"):
            # بعض المنصات ترفض دمج الصيغ أو الكوكيز، جرب أي صيغة متاحة
            opts["format"] = "best"
            return _run(platform, url, opts, "video")
        raise


def _run(platform, url, opts, kind):
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as exc:
            err = str(exc)
            if platform == "instagram" and ("empty media" in err or "login" in err.lower()):
                raise DownloadError(
                    "انستغرام يطلب تسجيل دخول حالياً.\n"
                    "الحل: ضع ملف كوكيز في bot/cookies/instagram.txt أو أرسل رابطاً لحساب عام."
                )
            raise DownloadError(f"فشل التحميل: {err}")

    candidates = set()
    if info:
        req = info.get("requested_downloads") or []
        for rd in req:
            fp = rd.get("filepath")
            if fp:
                candidates.add(fp)
        candidates.add(info.get("_filename") or "")

    path = None
    for c in candidates:
        if c and os.path.exists(c):
            path = c
            break
    if not path:
        # fallback: newest file matching pid prefix
        prefix = f"{kind}_{os.getpid()}_"
        files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(prefix)]
        if files:
            full = max([os.path.join(DOWNLOAD_DIR, f) for f in files], key=os.path.getmtime)
            if os.path.exists(full):
                path = full

    if not path or not os.path.exists(path):
        raise DownloadError("تعذر إيجاد الملف المحمّل.")

    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        os.remove(path)
        raise DownloadError("الملف أكبر من 50MB ولا يمكن إرساله عبر تيليغرام.")
    return path, info.get("title") if info else None


def cleanup(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def cleanup_old_files(max_age_hours=24):
    now = __import__("time").time()
    for f in os.listdir(DOWNLOAD_DIR):
        fp = os.path.join(DOWNLOAD_DIR, f)
        if os.path.isfile(fp) and os.path.getmtime(fp) < now - max_age_hours * 3600:
            try:
                os.remove(fp)
            except OSError:
                pass


class DownloadError(Exception):
    pass
