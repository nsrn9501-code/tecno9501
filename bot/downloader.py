import os
import time
import re
import logging

logger = logging.getLogger(__name__)
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
    if os.path.exists(p):
        return p
    # إذا ما في ملف كوكيز لكن فيه session ID بالمتغيرات البيئية، ننشئه تلقائياً
    if platform == "instagram":
        from .config import INSTAGRAM_SESSION_ID
        if INSTAGRAM_SESSION_ID:
            os.makedirs(COOKIES_DIR, exist_ok=True)
            with open(p, "w") as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# Auto-generated from INSTAGRAM_SESSION_ID\n\n")
                f.write(f".instagram.com\tTRUE\t/\tFALSE\t0\tsessionid\t{INSTAGRAM_SESSION_ID}\n")
            logger.info("تم إنشاء ملف كوكيز انستغرام تلقائياً من INSTAGRAM_SESSION_ID")
            return p
    return None


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
        "concurrent_fragment_downloads": 8,
        "http_chunk_size": 10 * 1024 * 1024,
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
    # أهم أولوية: صيغة معدنية (مدمجة) واحدة بـ H.264 (avc1) بامتداد mp4 —
    # لأنها تشتغل في تيليجرام مباشرة بدون تحويل (كما جربناها سابقاً).
    # وإلا DASH H.264، ثم أي فيديو (يُحوَّل لاحقاً إلى H.264). لا صوت فقط أبداً.
    fmt = (f"best[vcodec~='^(avc1|h264)'][ext=mp4][height<=?{max_height}]"
           f"/bestvideo[vcodec~='^(avc1|h264)'][ext=mp4][height<=?{max_height}]+bestaudio"
           f"/bestvideo[ext=mp4][height<=?{max_height}]+bestaudio"
           f"/bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best")
    opts.update({
        "format": fmt,
        "merge_output_format": "mp4",
        "format_sort": ["res", "vcodec:h264", "ext:mp4:m4a"],
    })
    if platform == "instagram":
        # انستغرام: نفضّل الصيغة المعدنية المدمجة H.264 دائماً،
        # ثم نلجأ للصيغة العامة كحل احتياطي مع إعادة ترميز.
        return _download_instagram_muxed(url, opts, fmt)
    try:
        return _run(platform, url, opts, "video")
    except DownloadError:
        if platform in ("tiktok", "facebook"):
            # بعض المنصات ترفض دمج الصيغ أو الكوكيز، جرب MP4 متاح (أقل عرضة للملفات التالفة)
            opts["format"] = "best[ext=mp4]/best"
            return _run(platform, url, opts, "video")
        raise


def _download_instagram_muxed(url, base_opts, fallback_fmt):
    """انستغرام: محاولة واحدة سريعة فقط بالصيغة الأفضل (H.264/MP4 ضمن الجودة المطلوبة).
    السرعة أولاً — لا فحص مسبق ولا تجارب متعددة متتالية تستهلك الدقائق."""
    trial = dict(base_opts)
    trial["format"] = fallback_fmt
    try:
        return _run(platform="instagram", url=url, opts=trial, kind="video")
    except DownloadError:
        # محاولة احتياطية واحدة فقط: أي MP4 متاح
        trial2 = dict(base_opts)
        trial2["format"] = "best[ext=mp4]/best"
        return _run(platform="instagram", url=url, opts=trial2, kind="video")


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


def _run_clean(platform, url, opts, kind, expected_codec):
    """نسخة من _run لكنها تتحقق أن الفيديو المحمّل بترميز H.264 فعلاً.
    نستخدمها لانستغرام لنضمن إرسال ملف يدعمه تيليجرام مباشرة."""
    path, title = _run(platform, url, opts, kind)
    if expected_codec:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        codec = (probe.stdout or "").strip()
        if codec != expected_codec:
            try:
                os.remove(path)
            except OSError:
                pass
            raise DownloadError(
                f"الصيغة المتاحة كانت {codec or 'غير معروفة'} وليست H.264؛ جرّبت صيغة أخرى."
            )
    return path, title




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
    audio_profile = ""
    for st in info.get("streams", []):
        if st.get("codec_type") == "video":
            vcodec = st.get("codec_name")
            width = st.get("width") or 0
            height = st.get("height") or 0
            pix_fmt = st.get("pix_fmt")
        elif st.get("codec_type") == "audio" and not acodec:
            acodec = st.get("codec_name")
            audio_profile = (st.get("profile") or "").lower()
    if vcodec != "h264" or not width or not height:
        return False
    if pix_fmt not in ("yuv420p", "yuvj420p"):
        return False
    if acodec and acodec not in ("aac", "mp3"):
        return False
    # ملاحظة: نحن نقبل HE-AAC أيضاً؛ التجربة أثبتت أنه يشتغل بعد التحويل.
    # نكتفي بضمان H.264 + yuv420p داخل MP4.
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


def _safe_limit_mb():
    """حد آمن أقل من حد تيليجرام (50MB) لضمان التشغيل الداخلي."""
    return MAX_FILE_SIZE - (2 * 1024 * 1024)  # 48MB


def _ffmpeg_transcode(path, out, extra=None, timeout=900, crf="24", preset="ultrafast"):
    """أمر ffmpeg قياسي: libx264 + yuv420p + faststart + AAC-LC.
    يقبل معاملات إضافية (مثل ضبط الأبعاد أو الـ bitrate).
    crf=None يعني الترميز بوضع bitrate ثابت (لا crf).
    preset=ultrafast = أسرع ترميز ممكن (الأولوية للسرعة على الحجم الصغير)."""
    cmd = [
        "ffmpeg", "-y", "-i", path,
    ]
    if extra:
        cmd.extend(extra)
    cmd.extend([
        "-c:v", "libx264", "-preset", preset, "-threads", "0",
    ])
    if crf:
        cmd.extend(["-crf", crf])
    cmd.extend([
        "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
        "-tag:v", "avc1",
        "-c:a", "aac", "-profile:a", "aac_low",
        "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart", "-f", "mp4", out,
    ])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return r


def ensure_telegram_compatible(path):
    """يضمن إرسال فيديو يدعمه تيليجرام، بأسرع مسار ممكن (السرعة أولاً):
    1) إن كان H.264/MP4 جاهزاً نعيده فوراً دون أي معالجة.
    2) غيره remux سريع (نسخ الفيديو + إعادة ترميز الصوت فقط).
    3) غير ذلك ترميز كامل واحد بـ ultrafast 480p كحد أقصى.
    لا مسارات متعددة بطيئة."""
    out = os.path.splitext(path)[0] + "_conv.mp4"
    if os.path.exists(out):
        os.remove(out)

    # المسار 1 (الأسرع — صفر معالجة): الملف أصلاً جاهز؟
    info = _ffprobe_info(path)
    if info and _video_is_ready(info):
        size = os.path.getsize(path)
        if size <= _safe_limit_mb():
            return path

    # المسار 2: remux سريع — نسخ الفيديو H.264 + إعادة ترميز الصوت إلى AAC-LC فقط.
    # (انستغرام غالباً H.264 + HE-AAC — تُحل في ثوانٍ لا دقائق).
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", path,
             "-c:v", "copy",
             "-c:a", "aac", "-profile:a", "aac_low",
             "-b:a", "128k", "-ar", "44100", "-ac", "2",
             "-threads", "0", "-movflags", "+faststart", "-f", "mp4", out],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        r = None
    if r is not None and r.returncode == 0 and os.path.exists(out):
        obj_info = _ffprobe_info(out)
        if obj_info and _video_is_ready(obj_info):
            size = os.path.getsize(out)
            if size <= _safe_limit_mb():
                return out
        if os.path.exists(out):
            os.remove(out)

    # المسار 3 (الوحيد الثقيل): ترميز كامل واحد بـ ultrafast، دقة ≤480 للسرعة.
    extra = ["-vf", "scale=-2:'min(480,ih)'", "-crf", "26"]
    r = _ffmpeg_transcode(path, out, extra=extra)
    if r is not None and r.returncode == 0 and os.path.exists(out):
        final_info = _ffprobe_info(out)
        if final_info and _video_is_ready(final_info):
            size = os.path.getsize(out)
            if size <= MAX_FILE_SIZE:
                return out
    if os.path.exists(out):
        os.remove(out)
    # آخر حل: نعيد الملف الأصلي — تيليجرام قد يتعامل معه أحياناً.
    return path


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
    vcodec = acodec = acodec_profile = None
    for st in info.get("streams", []):
        if st.get("codec_type") == "video":
            width = st.get("width") or 0
            height = st.get("height") or 0
            vcodec = st.get("codec_name")
        elif st.get("codec_type") == "audio" and acodec is None:
            acodec = st.get("codec_name")
            acodec_profile = st.get("profile")
    if not width or not height or duration <= 0:
        return None
    return {
        "duration": int(duration), "width": int(width), "height": int(height),
        "vcodec": vcodec, "acodec": acodec, "acodec_profile": acodec_profile,
    }


def fetch_qualities(url):
    """تجلب الصيغ المتاحة للرابط وتعيد قائمة [{height, label, ext}] مرتبة صعوداً."""
    platform = detect_platform(url)
    if not platform:
        return []
    opts = _base_opts(platform, "%(title)s.%(ext)s")
    opts["skip_download"] = True
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return []
    formats = info.get("formats") or []
    heights = set()
    for f in formats:
        h = f.get("height")
        vcodec = f.get("vcodec", "none")
        if h and vcodec != "none" and h > 0:
            heights.add(int(h))
    ordered = sorted(heights)
    qualities = []
    seen_labels = set()
    for h in ordered:
        if h <= 240:
            label = "240p 📉"
        elif h <= 360:
            label = "360p 📺"
        elif h <= 480:
            label = "480p 🎬"
        elif h <= 720:
            label = "720p HD ✨"
        elif h <= 1080:
            label = "1080p HD 🎉"
        else:
            label = f"{h}p 🌟"
        key = str(h)
        if key not in seen_labels:
            qualities.append({"height": h, "label": label})
            seen_labels.add(key)
    # add MP3 audio option
    qualities.append({"height": 0, "label": "🎵 صوت MP3"})
    return qualities
