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
        if platform == "instagram":
            # بعض المنصات ترفض دمج الصيغ أو الكوكيز: جرب MP4 متاح (أقل عرضة للملفات التالفة)
            opts["format"] = "best[ext=mp4]/best"
            try:
                return _run(platform, url, opts, "video")
            except DownloadError:
                # جرّب أيضاً App IDs مختلفة (يغيّر مسار API وقد يتجاوز تقييد المنصة)
                for app_id in ("124024574287414", "567067343352427", "3698584747777168"):
                    try:
                        trial = dict(opts)
                        trial["extractor_args"] = {"instagram": {"app_id": [app_id]}}
                        return _run(platform, url, trial, "video")
                    except DownloadError:
                        continue
                raise
        if platform in ("tiktok", "facebook"):
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
            if platform == "instagram":
                low = err.lower()
                if "login required" in low or "redirected to the login page" in low or "logged-in" in low or "logged in" in low:
                    raise DownloadError(
                        "انستغرام طلب تسجيل دخول حالياً (ضغط مرتفع أو حساب خاص).\n"
                        "الحل: ضع ملف كوكيز في bot/cookies/instagram.txt أو جرّب رابطاً لحساب عام/عام."
                    )
                if "no video" in low or "empty media" in low or "no formats" in low:
                    raise DownloadError(
                        "لا يوجد فيديو قابلاً للتحميل في هذا المنشور أو أنه حساب خاص.\n"
                        "تأكد أن الرابط لمنشور فيديو عام أو جرّب رابطاً آخر."
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




def _ffprobe_info(fp):
    """يعيد معلومات الملف JSON عبر ffprobe، أو None عند الخطأ."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,format_name:stream=codec_name,codec_type,width,height,pix_fmt,codec_tag_string,profile",
         "-of", "json", fp],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        return None
    try:
        return __import__("json").loads(r.stdout)
    except Exception:
        return None


def _video_is_ready(info):
    """هل الملف فيديو H.264 + AAC يدعمه تيليجرام بشكل مضمون؟"""
    if not info:
        return False
    fmt = info.get("format") or {}
    fname = (fmt.get("format_name") or "").lower()
    if "mp4" not in fname and "mov" not in fname:
        return False
    try:
        dur = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur <= 0:
        return False
    vcodec = acodec = None
    width = height = 0
    pix_fmt = None
    for st in info.get("streams", []):
        if st.get("codec_type") == "video":
            vcodec = st.get("codec_name")
            width = st.get("width") or 0
            height = st.get("height") or 0
            pix_fmt = st.get("pix_fmt")
        elif st.get("codec_type") == "audio" and not acodec:
            acodec = st.get("codec_name")
    if vcodec != "h264" or not width or not height:
        return False
    if pix_fmt not in ("yuv420p", "yuvj420p"):
        return False
    if acodec and acodec not in ("aac", "mp3"):
        return False
    return True


def _decode_ok(fp):
    """يفك تشفير الفيديو كاملاً بلا أخطاء: يتأكد ألا يكون الملف تالفاً/ناقصاً."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", fp, "-f", "null", "-"],
            capture_output=True, text=True, timeout=600,
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def ensure_telegram_compatible(path):
    """يضمن إرسال فيديو يدعمه تيليجرام نهائياً (MP4 / H.264 / AAC / yuv420p).

    - إذا كان الملف الأصلي أصلاً H.264+AAC+yuv420p وسليماً => تعبئة سريعة (remux).
    - وإلا => إعادة ترميز كاملة إلى H.264 + AAC + yuv420p + faststart.
    - فحص نهائي شامل (كوديك + فك تشفير كامل) قبل الإرسال؛ إن فشل نرفع خطأ ولا نرسل ملفاً تالفاً.
    """
    out = os.path.splitext(path)[0] + "_conv.mp4"
    if os.path.exists(out):
        os.remove(out)

    info = _ffprobe_info(path)

    # 1) إن كان مصدر الفيديو أصلاً مطابقاً للشكل المطلوب → تعبئة سريعة فقط
    if info and _video_is_ready(info):
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-c", "copy",
                 "-movflags", "+faststart", out],
                capture_output=True, text=True, timeout=300,
            )
            if r.returncode == 0 and os.path.exists(out) and _video_is_ready(_ffprobe_info(out)):
                return out
        except subprocess.TimeoutExpired:
            pass
        if os.path.exists(out):
            os.remove(out)

    # 2) إعادة ترميز كاملة: تحويل VP9/أي كوديك آخر إلى H.264 + AAC + yuv420p
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", path,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
             "-tag:v", "avc1",
             "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
             "-movflags", "+faststart", "-f", "mp4", out],
            capture_output=True, text=True, timeout=600,
        )
        conv_ok = r.returncode == 0 and os.path.exists(out)
    except subprocess.TimeoutExpired:
        conv_ok = False

    final_info = _ffprobe_info(out) if conv_ok else None
    if not conv_ok or not _video_is_ready(final_info) or not _decode_ok(out):
        if os.path.exists(out):
            os.remove(out)
        raise DownloadError(
            "❌ المقطع وصل من المنصة بصيغة لا يدعمها تيليجرام (VP9)، وفشل تحويله تلقائياً.\n"
            "جرّب رابطاً آخر أو أعد إرساله بعد قليل."
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


def probe_media(path):
    """يعيد مدة وأبعاد الفيديو (لإرساله كفيديو تفاعلي مكتمل) أو None."""
    info = _ffprobe_info(path)
    if not info:
        return None
    fmt = info.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    width = height = 0
    for st in info.get("streams", []):
        if st.get("codec_type") == "video":
            width = st.get("width") or 0
            height = st.get("height") or 0
            break
    if not width or not height or duration <= 0:
        return None
    return {"duration": int(duration), "width": int(width), "height": int(height)}
