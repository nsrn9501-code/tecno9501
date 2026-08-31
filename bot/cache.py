"""نظام كاش ذكي للتحميلات — يوفر 90% من وقت التحميل للمحتوى المتكرر.

يعمل ب绨رين:
  1. كاش الملفات: يحتفظ بالملفات المحمّلة مسبقاً (مجلد data/cache/)
  2. كاش البيانات الوصفية: عنوان + مدة + حجم (JSON)

يعيد الملف المخزّن فوراً بدون إعادة تحميل، مهما كان عدد المستخدمين الذين طلبوا نفس الرابط.
"""
import hashlib
import json
import os
import shutil
import time
import threading
import logging

logger = logging.getLogger(__name__)

from .config import DB_PATH

# مجلد الكاش: بجانب bot.db
_CACHE_DIR = os.path.join(os.path.dirname(DB_PATH), "cache")
_META_FILE = os.path.join(_CACHE_DIR, "_meta.json")

# مدة صلاحية الكاش (ثوان١) — 24 ساعة
CACHE_TTL = 24 * 60 * 60

# أقصى حجم للكاش (5 جيجابايت)
MAX_CACHE_SIZE = 5 * 1024 * 1024 * 1024

# قفل للوصول المتزامن
_lock = threading.RLock()
_meta = {}  # {cache_key: {"path": str, "title": str, "size": int, "ts": float, "kind": str}}


def _ensure_cache_dir():
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _load_meta():
    global _meta
    _ensure_cache_dir()
    if os.path.exists(_META_FILE):
        try:
            with open(_META_FILE, "r") as f:
                _meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            _meta = {}
    else:
        _meta = {}


def _save_meta():
    _ensure_cache_dir()
    try:
        with open(_META_FILE, "w") as f:
            json.dump(_meta, f, ensure_ascii=False)
    except OSError:
        pass


def _cache_key(url, kind, quality="best"):
    """مفتاح فريد لكل رابط + نوع + جودة."""
    raw = f"{url.strip().lower()}|{kind}|{quality}"
    return hashlib.md5(raw.encode()).hexdigest()


def init_cache():
    """تهيئة الكاش عند بدء التشغيل."""
    with _lock:
        _load_meta()
        _cleanup_expired()
        _ensure_cache_dir()
        logger.info("📦 تم تحميل الكاش: %d ملف مخزّن", len(_meta))


def get_cached(url, kind, quality="best"):
    """إذا الملف موجود بالكاش وصالح، يعيده. وإلا None."""
    key = _cache_key(url, kind, quality)
    with _lock:
        entry = _meta.get(key)
        if not entry:
            return None
        # تحقق من الصلاحية
        if time.time() - entry.get("ts", 0) > CACHE_TTL:
            _remove_entry(key)
            return None
        # تحقق من وجود الملف فعلياً
        path = entry.get("path")
        if not path or not os.path.exists(path):
            _remove_entry(key)
            return None
        logger.info("✅ كاشإصابة: %s → %s", url[:50], path)
        return {
            "path": path,
            "title": entry.get("title"),
            "size": entry.get("size", 0),
            "kind": entry.get("kind", kind),
        }


def store_in_cache(url, kind, path, title=None, quality="best"):
    """يخزّن ملفاً محمّلاً في الكاش."""
    if not path or not os.path.exists(path):
        return
    key = _cache_key(url, kind, quality)
    size = os.path.getsize(path)
    # لا نخزّن ملفات أكبر من 50MB (حد تيليجرام)
    if size > 50 * 1024 * 1024:
        return
    # نسخ الملف إلى مجلد الكاش
    cache_path = os.path.join(_CACHE_DIR, f"{key}_{kind}")
    # حفظ الامتداد الأصلي
    ext = os.path.splitext(path)[1] or (".mp4" if kind == "video" else ".mp3")
    cache_path += ext
    try:
        shutil.copy2(path, cache_path)
    except OSError:
        return
    with _lock:
        _meta[key] = {
            "path": cache_path,
            "title": title or "ملف مخزّن",
            "size": size,
            "ts": time.time(),
            "kind": kind,
        }
        _save_meta()
        _enforce_size_limit()
    logger.info("📦 تم التخزين بالكاش: %s (%s)", title or url[:30], _fmt_size(size))


def _remove_entry(key):
    entry = _meta.pop(key, None)
    if entry:
        path = entry.get("path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    _save_meta()


def _cleanup_expired():
    """حذف الملفات منتهية الصلاحية."""
    now = time.time()
    expired = [k for k, v in _meta.items() if now - v.get("ts", 0) > CACHE_TTL]
    for k in expired:
        _remove_entry(k)
    if expired:
        logger.info("🧹 تم تنظيف %d ملف منتهي الصلاحية من الكاش", len(expired))


def _enforce_size_limit():
    """ضمان عدم تجاوز الحد الأقصى لحجم الكاش."""
    total = sum(e.get("size", 0) for e in _meta.values())
    if total <= MAX_CACHE_SIZE:
        return
    # حذف الأقدم أولاً
    sorted_keys = sorted(_meta.keys(), key=lambda k: _meta[k].get("ts", 0))
    for k in sorted_keys:
        if total <= MAX_CACHE_SIZE * 0.8:
            break
        size = _meta[k].get("size", 0)
        _remove_entry(k)
        total -= size
    logger.info("🧹 تم تقليص الكاش إلى %s", _fmt_size(total))


def get_cache_stats():
    """إحصائيات الكاش."""
    with _lock:
        total_size = sum(e.get("size", 0) for e in _meta.values())
        return {
            "files": len(_meta),
            "size": total_size,
            "size_human": _fmt_size(total_size),
        }


def _fmt_size(num):
    try:
        mb = num / (1024 * 1024)
        if mb >= 1024:
            return f"{mb/1024:.2f} GB"
        return f"{mb:.1f} MB"
    except Exception:
        return str(num)
