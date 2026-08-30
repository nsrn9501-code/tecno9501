"""طبقة تخزين بدون SQLite.

تعتمد على ملف JSON واحد (بديل آمن عن قاعدة SQLite) + قفل في الذاكرة.
نفس الواجهة البرمجية تماماً (نفس أسماء الدوال ونفس القيم المُعادة)،
لذلك لا يتغيّر أي شيء في باقي البوت، ويعمل على أي استضافة مجانية
بدون مشاكل "database is locked".

البيانات تبقى في الذاكرة أثناء التشغيل وتُحفظ في الملف فور كل عملية
كتابة (كتابة مؤقتة ثم استبدال ذري) حتى لا تضيع النقاط/البيانات عند
إعادة تشغيل السيرفر.
"""

import datetime
import json
import os
import secrets
import threading
import time

from .config import (
    DAILY_REWARD_POINTS,
    DB_PATH,
    GIFT_POINTS,
    OWNER_ID,
    POINTS_PER_REFERRAL,
    VIP_THRESHOLD,
)

# مسار ملف التخزين الجديد (JSON) — لا يتعارض مع bot.db القديم إن وُجد
STORE_PATH = DB_PATH + ".json"

# قفل واحد يمنع أي تداخل بين الخيوط (القراءة والكتابة معاً)
_LOCK = threading.RLock()

# ---- البنية الحية للبيانات في الذاكرة ----
_data = {
    "users": {},            # str(user_id) -> dict (id, username, first_name, points, ...)
    "downloads": [],        # قائمة سجلات التحميل (id, user_id, kind, title, filesize, created_at)
    "settings": {},         # key -> value (نصوص)
    "referrals": [],        # قائمة (id, referrer_id, referee_id, created_at)
    "daily_rewards": {},    # str(user_id) -> قائمة تواريخ حصل فيها على المكافأة
    "gift_links": {},       # code -> dict (id, code, owner_id, max_uses, used, active, points, created_at)
    "gift_uses": [],        # قائمة (link_id, user_id)
    "daily_usage": {},      # str(user_id) -> {date: {"link": n, "search": n}}
    "rate_bans": {},        # str(user_id) -> until (epoch float)
    "user_prefs": {},       # str(user_id) -> {"fact_category": str, "welcomed": 0/1}
    "_next_id": 1,
}

_DEFAULT_SETTINGS = {
    "gift_default_points": "10",
    "channel_id": "",
    "channel_name": "",
    "channel_url": "",
    "daily_limit_free": "3",
    "daily_limit_vip": "20",
    "daily_link_limit_free": "5",
    "daily_link_limit_vip": "15",
    "daily_search_limit_free": "5",
    "daily_search_limit_vip": "15",
}


# ------------------------- أدوات داخلية -------------------------

def _now_iso():
    """طابع زمني محلي بصيغة نصية قابلة للمقارنة."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _parse_dt(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _next_id_locked():
    _data["_next_id"] += 1
    return _data["_next_id"]


def _save_locked():
    """يحفظ البيانات في ملف JSON (كتابة مؤقتة + استبدال ذري)."""
    folder = os.path.dirname(STORE_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE_PATH)


def _save():
    with _LOCK:
        _save_locked()


def _empty_store():
    return {
        "users": {},
        "downloads": [],
        "settings": dict(_DEFAULT_SETTINGS),
        "referrals": [],
        "daily_rewards": {},
        "gift_links": {},
        "gift_uses": [],
        "daily_usage": {},
        "rate_bans": {},
        "user_prefs": {},
        "_next_id": 1,
    }


def _load_locked():
    """يحمل البيانات من الملف؛ أي مفاتيح ناقصة يُعيد إنشاءها افتراضياً."""
    global _data
    if not os.path.exists(STORE_PATH):
        return False
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        return False
    base = _empty_store()
    base.update(loaded)
    for key in _empty_store():
        if key not in base:
            base[key] = _empty_store()[key]
    if not isinstance(base["settings"], dict):
        base["settings"] = dict(_DEFAULT_SETTINGS)
    base["settings"].setdefault("gift_default_points", "10")
    _data = base
    return True


def _migrate_from_sqlite_locked():
    """مرة واحدة فقط: إن لم يوجد ملف JSON ووُجدت قاعدة SQLite قديمة،
    ننقل بياناتها إلى التخزين الجديد حتى لا تضيع النقاط/المستخدمون."""
    if not os.path.exists(DB_PATH):
        return
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        def rows(table):
            try:
                return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
            except sqlite3.Error:
                return []

        users = rows("users")
        for r in users:
            _data["users"][str(r["id"])] = {
                "id": r["id"],
                "username": r.get("username"),
                "first_name": r.get("first_name"),
                "points": r.get("points", 0) or 0,
                "is_vip": r.get("is_vip", 0) or 0,
                "is_banned": r.get("is_banned", 0) or 0,
                "invited_by": r.get("invited_by"),
                "referrals": r.get("referrals", 0) or 0,
                "total_downloads": r.get("total_downloads", 0) or 0,
                "audio_downloads": r.get("audio_downloads", 0) or 0,
                "video_downloads": r.get("video_downloads", 0) or 0,
                "joined_at": r.get("joined_at") or _now_iso(),
            }
        for r in rows("downloads"):
            _data["downloads"].append({
                "id": r.get("id", _next_id_locked()),
                "user_id": r["user_id"],
                "kind": r.get("kind", ""),
                "title": r.get("title"),
                "filesize": r.get("filesize", 0) or 0,
                "created_at": r.get("created_at") or _now_iso(),
            })
        for r in rows("settings"):
            _data["settings"][r["key"]] = r.get("value")
        for r in rows("referrals"):
            _data["referrals"].append({
                "id": r.get("id", _next_id_locked()),
                "referrer_id": r["referrer_id"],
                "referee_id": r["referee_id"],
                "created_at": r.get("created_at") or _now_iso(),
            })
        for r in rows("daily_rewards"):
            _data["daily_rewards"].setdefault(str(r["user_id"]), [])
            _data["daily_rewards"][str(r["user_id"])].append(r["reward_date"])
        for r in rows("gift_links"):
            _data["gift_links"][r["code"]] = {
                "id": r.get("id", _next_id_locked()),
                "code": r["code"],
                "owner_id": r["owner_id"],
                "max_uses": r.get("max_uses", 1) or 1,
                "used": r.get("used", 0) or 0,
                "active": r.get("active", 1) or 0,
                "points": r.get("points", GIFT_POINTS) or GIFT_POINTS,
                "created_at": r.get("created_at") or _now_iso(),
            }
        for r in rows("gift_uses"):
            _data["gift_uses"].append({"link_id": r["link_id"], "user_id": r["user_id"]})
        for r in rows("daily_usage"):
            _data["daily_usage"].setdefault(str(r["user_id"]), {})
            _data["daily_usage"][str(r["user_id"])].setdefault(r["usage_date"], {})
            _data["daily_usage"][str(r["user_id"])][r["usage_date"]][r["usage_type"]] = r.get("count", 0) or 0
        for r in rows("rate_bans"):
            _data["rate_bans"][str(r["user_id"])] = r["until"]
        for r in rows("user_prefs"):
            _data["user_prefs"][str(r["user_id"])] = {
                "fact_category": r.get("fact_category", "both"),
                "welcomed": r.get("welcomed", 0) or 0,
            }
        # أكبر id موجود لضمان عدم تكرار الأرقام
        max_ids = [0]
        max_ids += [u.get("id", 0) or 0 for u in _data["users"].values()]
        max_ids += [d.get("id", 0) or 0 for d in _data["downloads"]]
        max_ids += [r.get("id", 0) or 0 for r in _data["referrals"]]
        max_ids += [g.get("id", 0) or 0 for g in _data["gift_links"].values()]
        _data["_next_id"] = max(max_ids) + 1
        conn.close()
    except Exception:
        # أي خطأ أثناء النقل لا يمنع الإقلاع أبداً
        pass


# ------------------------- init -------------------------

def init_db():
    """يحمّل البيانات (أو يبنيها من الصفر) ويضمن الإعدادات الافتراضية."""
    global _data
    with _LOCK:
        if not _load_locked():
            _data = _empty_store()
            _migrate_from_sqlite_locked()
        for key, value in _DEFAULT_SETTINGS.items():
            _data["settings"].setdefault(key, value)
        _save_locked()


def init_db_prefs():
    """متوافقة مع الكود القديم — لا تحتاج لأي شيء لأن التفضيلات جزء من JSON."""
    with _LOCK:
        _data.setdefault("user_prefs", {})
        _save_locked()


# ------------------------- users -------------------------

def _new_user_locked(user_id, username=None, first_name=None, invited_by=None):
    return {
        "id": user_id,
        "username": username,
        "first_name": first_name,
        "points": 0,
        "is_vip": 0,
        "is_banned": 0,
        "invited_by": None if invited_by is None else invited_by,
        "referrals": 0,
        "total_downloads": 0,
        "audio_downloads": 0,
        "video_downloads": 0,
        "joined_at": _now_iso(),
    }


def get_user(user_id):
    with _LOCK:
        u = _data["users"].get(str(user_id))
        return dict(u) if u else None


def create_user(user_id, username=None, first_name=None, invited_by=None):
    """ينشئ مستخدماً جديداً؛ يعيد True إن احتُسبت دعوة (نفس السلوك القديم)."""
    credited = False
    with _LOCK:
        key = str(user_id)
        if key in _data["users"]:
            return False
        _data["users"][key] = _new_user_locked(user_id, username, first_name, invited_by)
        if invited_by and invited_by != user_id and str(invited_by) in _data["users"]:
            inv = _data["users"][str(invited_by)]
            _data["users"][key]["invited_by"] = invited_by
            inv["points"] = inv.get("points", 0) + POINTS_PER_REFERRAL
            inv["referrals"] = inv.get("referrals", 0) + 1
            _data["referrals"].append({
                "id": _next_id_locked(),
                "referrer_id": invited_by,
                "referee_id": user_id,
                "created_at": _now_iso(),
            })
            if inv["points"] >= VIP_THRESHOLD:
                inv["is_vip"] = 1
            credited = True
        _save_locked()
    return credited


def get_or_create_user(user_id, username=None, first_name=None, invited_by=None):
    u = get_user(user_id)
    is_new = False
    if not u:
        create_user(user_id, username, first_name, invited_by)
        is_new = True
        u = get_user(user_id)
        credited = is_new
    else:
        with _LOCK:
            _data["users"][str(user_id)]["username"] = username
            _data["users"][str(user_id)]["first_name"] = first_name
            _save_locked()
        credited = False
    return u, (credited, is_new)


def add_points(user_id, points):
    with _LOCK:
        key = str(user_id)
        u = _data["users"].get(key)
        if not u:
            return
        u["points"] = u.get("points", 0) + points
        if u["points"] >= VIP_THRESHOLD:
            u["is_vip"] = 1
        _save_locked()


def is_vip(user_id):
    u = get_user(user_id)
    return bool(u and u["is_vip"])


# ------------------------- التحميلات -------------------------

def add_download_stats(user_id, kind, title=None, filesize=0):
    with _LOCK:
        u = _data["users"].get(str(user_id))
        if u:
            if kind == "audio":
                u["audio_downloads"] = u.get("audio_downloads", 0) + 1
            else:
                u["video_downloads"] = u.get("video_downloads", 0) + 1
            u["total_downloads"] = u.get("total_downloads", 0) + 1
        _data["downloads"].append({
            "id": _next_id_locked(),
            "user_id": user_id,
            "kind": kind,
            "title": title or "تحميل",
            "filesize": filesize or 0,
            "created_at": _now_iso(),
        })
        _save_locked()


def recent_downloads(user_id, limit=10):
    with _LOCK:
        rows = [dict(d) for d in _data["downloads"] if d["user_id"] == user_id]
        rows.sort(key=lambda d: d["id"], reverse=True)
        return rows[:limit]


def _since_iso(days=1):
    return (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")


def user_daily_counts(user_id):
    """عدد التحميلات (الكلي/صوتي/فيديو) خلال آخر 24 ساعة."""
    with _LOCK:
        cutoff = _since_iso(1)
        total = audio = video = 0
        for d in _data["downloads"]:
            if d["user_id"] != user_id:
                continue
            created = _parse_dt(d.get("created_at"))
            if created is None or created < _parse_dt(cutoff):
                continue
            total += 1
            if d.get("kind") == "audio":
                audio += 1
            else:
                video += 1
    return {"total": total, "audio": audio, "video": video}


def daily_download_count(user_id, kind=None):
    with _LOCK:
        cutoff = _parse_dt(_since_iso(1))
        count = 0
        for d in _data["downloads"]:
            if d["user_id"] != user_id:
                continue
            created = _parse_dt(d.get("created_at"))
            if created is not None and created >= cutoff:
                count += 1
    return count


# ------------------------- روابط الهدايا -------------------------

def create_gift_link(owner_id, max_uses, points=GIFT_POINTS):
    code = secrets.token_hex(4)
    with _LOCK:
        link = {
            "id": _next_id_locked(),
            "code": code,
            "owner_id": owner_id,
            "max_uses": max_uses,
            "used": 0,
            "active": 1,
            "points": points,
            "created_at": _now_iso(),
        }
        _data["gift_links"][code] = link
        _save_locked()
    return code


def redeem_gift_link(code, user_id):
    """يعيد (نجاح، نقاط، رسالة) عند استعمال رابط هدية (نفس السلوك القديم)."""
    with _LOCK:
        row = _data["gift_links"].get(code)
        if not row:
            return False, 0, "الرابط غير صحيح."
        if not row["active"]:
            return False, 0, "هذا الرابط معطّل."
        if row["used"] >= row["max_uses"]:
            return False, 0, "هذا الرابط انتهت صلاحيته (اكتمل عدد الاستخدامات)."
        already = any(u["link_id"] == row["id"] and u["user_id"] == user_id for u in _data["gift_uses"])
        if already:
            return False, 0, "لقد استخدمت هذا الرابط من قبل."
        row["used"] += 1
        if row["used"] >= row["max_uses"]:
            row["active"] = 0
        _data["gift_uses"].append({"link_id": row["id"], "user_id": user_id})
        points = row["points"]
        _save_locked()
    add_points(user_id, points)
    return True, points, "تم تفعيل الهدية بنجاح!"


# ------------------------- الإعدادات -------------------------

def get_setting(key, default=""):
    with _LOCK:
        return _data["settings"].get(key, default)


def set_setting(key, value):
    with _LOCK:
        _data["settings"][key] = value
        _save_locked()


# ------------------------- الإحصائيات والتحكم -------------------------

def all_users():
    with _LOCK:
        rows = [dict(u) for u in _data["users"].values()]
        rows.sort(key=lambda u: u.get("total_downloads", 0), reverse=True)
        return rows


def total_stats():
    with _LOCK:
        users = len(_data["users"])
        downloads = len(_data["downloads"])
        audio = sum(1 for d in _data["downloads"] if d.get("kind") == "audio")
        video = downloads - audio
        vips = sum(1 for u in _data["users"].values() if u.get("is_vip"))
    return {"users": users, "downloads": downloads, "audio": audio, "video": video, "vips": vips}


def set_vip(user_id, status):
    with _LOCK:
        u = _data["users"].get(str(user_id))
        if u:
            u["is_vip"] = 1 if status else 0
            _save_locked()


def set_banned(user_id, status):
    with _LOCK:
        u = _data["users"].get(str(user_id))
        if u:
            u["is_banned"] = 1 if status else 0
            _save_locked()


# ------------------------- حدود التحميل اليومية -------------------------

def _today():
    return datetime.date.today().isoformat()


def usage_today(user_id, usage_type):
    with _LOCK:
        day = _data["daily_usage"].get(str(user_id), {}).get(_today(), {})
        return day.get(usage_type, 0)


def increment_usage(user_id, usage_type):
    with _LOCK:
        _data["daily_usage"].setdefault(str(user_id), {})
        _data["daily_usage"][str(user_id)].setdefault(_today(), {})
        _data["daily_usage"][str(user_id)][_today()][usage_type] = (
            _data["daily_usage"][str(user_id)][_today()].get(usage_type, 0) + 1
        )
        _save_locked()


def usage_limit(uid, usage_type):
    """يعيد الحد اليومي حسب النوع (روابط/بحث) وحالة VIP."""
    u = get_user(uid)
    is_vip = bool(u and u["is_vip"])
    prefix = f"daily_{usage_type}_limit"
    key = f"{prefix}_vip" if is_vip else f"{prefix}_free"
    default = {"link": "5", "search": "5"}[usage_type]
    return int(get_setting(key, default))


def can_use(uid, usage_type):
    """(ok, msg) — يفحص الحد اليومي للروابط أو البحث."""
    if uid == OWNER_ID:
        return True, ""
    limit = usage_limit(uid, usage_type)
    used = usage_today(uid, usage_type)
    if used >= limit:
        label = "روابط" if usage_type == "link" else "بحث بالاسم"
        return False, f"📊 وصلت للحد اليومي ({limit} {label}).\nارجع غداً أو ترقى إلى VIP 👑 لحد أعلى."
    return True, ""


def consume_usage(uid, usage_type):
    """يستهلك وحدة من حد اليوم: يرجع (ok, msg) ويزيد العداد إن كان متاحاً."""
    ok, msg = can_use(uid, usage_type)
    if not ok:
        return False, msg
    increment_usage(uid, usage_type)
    return True, ""


# ------------------------- تقييد السرعة -------------------------

def set_rate_ban(user_id, seconds=1800):
    until = time.time() + seconds
    with _LOCK:
        _data["rate_bans"][str(user_id)] = until
        _save_locked()
    return until


def clear_rate_ban(user_id):
    with _LOCK:
        _data["rate_bans"].pop(str(user_id), None)
        _save_locked()


def get_rate_ban(user_id):
    """يعيد (متبقي_ثواني) أو 0 إن لم يكن مقيّداً."""
    if user_id == OWNER_ID:
        return 0
    with _LOCK:
        until = _data["rate_bans"].get(str(user_id))
    if not until:
        return 0
    left = until - time.time()
    if left <= 0:
        clear_rate_ban(user_id)
        return 0
    return int(left)


# ------------------------- تفضيلات المستخدمين -------------------------

def set_fact_category(user_id, category):
    with _LOCK:
        _data["user_prefs"][str(user_id)] = {"fact_category": category, "welcomed": 0}
        _save_locked()


def get_fact_welcomed(user_id):
    with _LOCK:
        p = _data["user_prefs"].get(str(user_id))
        return bool(p and p.get("welcomed"))


def mark_fact_welcomed(user_id):
    with _LOCK:
        p = _data["user_prefs"].get(str(user_id))
        if p is not None:
            p["welcomed"] = 1
            _save_locked()


def get_fact_category(user_id):
    with _LOCK:
        p = _data["user_prefs"].get(str(user_id))
        return p["fact_category"] if p else "both"


# ------------------------- فهرس تقدم المعلومات -------------------------

def get_fact_offset(user_id, category):
    with _LOCK:
        value = _data["settings"].get(f"fact_offset_{category}_{user_id}")
    try:
        return int(value) if value else 0
    except (TypeError, ValueError):
        return 0


def set_fact_offset(user_id, category, idx):
    set_setting(f"fact_offset_{category}_{user_id}", str(idx))


# ------------------------- المكافأة اليومية -------------------------

def claim_daily_reward(user_id):
    """يضيف مكافأة يومية مرة واحدة فقط في اليوم. يعيد True إن نجح."""
    today = _today()
    with _LOCK:
        dates = _data["daily_rewards"].setdefault(str(user_id), [])
        if today in dates:
            return False
        dates.append(today)
        _save_locked()
    add_points(user_id, DAILY_REWARD_POINTS)
    return True


def last_daily_claim(user_id):
    with _LOCK:
        dates = _data["daily_rewards"].get(str(user_id), [])
        return dates[-1] if dates else None
