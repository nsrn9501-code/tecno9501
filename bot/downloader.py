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
            # بعض المنصات ترفض دمج الصيغ أو الكوكيز، جرب MP4 متاح (أقل عرضة للملفات التالفة)
            opts["format"] = "best[ext=mp4]/best"
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




def ensure_telegram_compatible(path):
    """يضمن أن الفيديو يُرسل بصيغة يدعمها تيليجرام (MP4 / H.264 / AAC / yuv420p).

    الخطوات:
    1) تعبئة سريعة (remux) ثم فحص دقيق للناتج: إن كان H.264 + AAC + yuv420p
       وسليماً (مدة وعرض وارتفاع صحيحة) نستخدمه كما هو.
    2) إن لم يكن سليماً → إعادة ترميز كاملة إلى H.264 + AAC (yuv420p + faststart).
    3) فحص الناتج النهائي؛ إن كان تالفاً نرفع DownloadError بدل إرسال ملف مكسور.
    """
    out = os.path.splitext(path)[0] + "_conv.mp4"
    if os.path.exists(out):
        os.remove(out)

    def _ffprobe_info(fp):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_name,codec_type,width,height,pix_fmt,codec_tag_string",
             "-of", "json", fp],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None
        try:
            return __import__("json").loads(r.stdout)
        except Exception:
            return None

    def _video_ok(info):
        """فحص شامل: هل الملف فيديو MP4 سليم يدعمه تيليجرام؟"""
        if not info:
            return False
        fmt = info.get("format") or {}
        try:
            dur = float(fmt.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        if dur <= 0:
            return False
        vcodec = acodec = None
        width = height = 0
        pix_fmt = None
        tag = None
        for st in info.get("streams", []):
            if st.get("codec_type") == "video":
                vcodec = st.get("codec_name")
                width = st.get("width") or 0
                height = st.get("height") or 0
                pix_fmt = st.get("pix_fmt")
                tag = (st.get("codec_tag_string") or "").lower()
            elif st.get("codec_type") == "audio" and not acodec:
                acodec = st.get("codec_name")
        if vcodec != "h264" or not width or not height:
            return False
        if pix_fmt not in ("yuv420p", "yuvj420p"):
            return False
        if acodec and acodec not in ("aac", "mp3"):
            return False
        # تيليجرام يقبل avc1; بعض الملفات تأتي بوسم hvc1/mp4v أو بدون وسم سليم
        if tag and tag not in ("avc1", "isom", "mp42", "mp41", ""):
            return False
        return True

    # 1) محاولة النسخ المباشر (remux): سريع إن كان الملف سليماً أصلاً
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-c", "copy",
             "-movflags", "+faststart", out],
            capture_output=True, text=True, timeout=300,
        )
        remux_ok = r.returncode == 0 and os.path.exists(out)
    except subprocess.TimeoutExpired:
        remux_ok = False

    if remux_ok and _video_ok(_ffprobe_info(out)):
        return out
    if os.path.exists(out):
        os.remove(out)

    # 2) إعادة ترميز كاملة إلى H.264 + AAC + yuv420p + faststart
    #    (يصلح الملفات التالفة/الناقصة وأكواد البكسل غير المدعومة من انستغرام)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", path,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
             "-movflags", "+faststart", out],
            capture_output=True, text=True, timeout=600,
        )
        conv_ok = r.returncode == 0 and os.path.exists(out)
    except subprocess.TimeoutExpired:
        conv_ok = False

    if not conv_ok or not _video_ok(_ffprobe_info(out)):
        if os.path.exists(out):
            os.remove(out)
        raise DownloadError(
            "❌ المقطع وصل ناقصاً أو تالفاً من المنصة ولا يمكن إصلاحه. جرّب رابطاً آخر أو أعد إرسال الرابط بعد قليل."
        )
    return out


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
