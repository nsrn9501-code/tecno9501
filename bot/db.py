import sqlite3
import threading
from contextlib import contextmanager

from .config import DAILY_REWARD_POINTS, DB_PATH, GIFT_POINTS, OWNER_ID, POINTS_PER_REFERRAL, VIP_THRESHOLD

# ─── Singleton connection + Lock (fixes "database is locked" on PythonAnywhere) ───
_lock = threading.Lock()
_conn = None


def _get_conn():
    """Get or create a single shared SQLite connection."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=120)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA synchronous=NORMAL;")
        _conn.execute("PRAGMA busy_timeout=30000;")
        _conn.execute("PRAGMA cache_size=-8000;")  # 8MB cache
    return _conn


@contextmanager
def cursor():
    """Thread-safe cursor using a single shared connection + Lock."""
    with _lock:
        conn = _get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db():
    with cursor() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                points INTEGER DEFAULT 0,
                is_vip INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                invited_by INTEGER,
                referrals INTEGER DEFAULT 0,
                total_downloads INTEGER DEFAULT 0,
                audio_downloads INTEGER DEFAULT 0,
                video_downloads INTEGER DEFAULT 0,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                kind TEXT,
                title TEXT,
                filesize INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referee_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS daily_rewards (
                user_id INTEGER,
                reward_date TEXT,
                PRIMARY KEY (user_id, reward_date)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS gift_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                owner_id INTEGER,
                max_uses INTEGER,
                used INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                points INTEGER DEFAULT 10,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS gift_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id INTEGER,
                user_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id INTEGER,
                usage_date TEXT,
                usage_type TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, usage_date, usage_type)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS rate_bans (
                user_id INTEGER PRIMARY KEY,
                until REAL
            )
        ''')
        # user_prefs table — created in init_db to avoid separate open/close
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_prefs (
                user_id INTEGER PRIMARY KEY,
                fact_category TEXT DEFAULT 'both',
                welcomed INTEGER DEFAULT 0
            )
        ''')
        # migration: add downloads detail columns if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(downloads)").fetchall()]
        if "title" not in cols:
            conn.execute("ALTER TABLE downloads ADD COLUMN title TEXT")
        if "filesize" not in cols:
            conn.execute("ALTER TABLE downloads ADD COLUMN filesize INTEGER")
        cols2 = [r[1] for r in conn.execute("PRAGMA table_info(user_prefs)").fetchall()]
        if "welcomed" not in cols2:
            conn.execute("ALTER TABLE user_prefs ADD COLUMN welcomed INTEGER DEFAULT 0")
        # Ensure default settings
        for k, v in [
            ('gift_default_points', '10'),
            ('channel_id', ''),
            ('channel_name', ''),
            ('channel_url', ''),
            ('daily_limit_free', '3'),
            ('daily_limit_vip', '20'),
            ('daily_link_limit_free', '5'),
            ('daily_link_limit_vip', '15'),
            ('daily_search_limit_free', '5'),
            ('daily_search_limit_vip', '15'),
        ]:
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))


# ---- users ----
def get_user(user_id):
    with cursor() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(user_id, username=None, first_name=None, invited_by=None):
    credited = False
    with cursor() as conn:
        cur = conn.execute(
            '''
                INSERT OR IGNORE INTO users (id, username, first_name, invited_by)
                VALUES (?,?,?,?)
            ''', (user_id, username, first_name, invited_by),
        )
        inserted = cur.rowcount > 0
        if inserted and invited_by and invited_by != user_id:
            conn.execute("UPDATE users SET invited_by=? WHERE id=?", (invited_by, user_id))
            conn.execute("UPDATE users SET points = points + ? WHERE id=?",
                         (POINTS_PER_REFERRAL, invited_by))
            conn.execute("UPDATE users SET referrals = referrals + 1 WHERE id=?", (invited_by,))
            conn.execute("INSERT INTO referrals (referrer_id, referee_id) VALUES (?,?)",
                         (invited_by, user_id))
            ref = conn.execute("SELECT points FROM users WHERE id=?", (invited_by,)).fetchone()
            if ref and ref["points"] >= VIP_THRESHOLD:
                conn.execute("UPDATE users SET is_vip=1 WHERE id=?", (invited_by,))
            credited = True
    return credited


def claim_daily_reward(user_id):
    today = _today()
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM daily_rewards WHERE user_id=? AND reward_date=?", (user_id, today)
        ).fetchone()
        if row:
            return False
        conn.execute(
            "INSERT INTO daily_rewards (user_id, reward_date) VALUES (?,?)", (user_id, today)
        )
    add_points(user_id, DAILY_REWARD_POINTS)
    return True


def last_daily_claim(user_id):
    with cursor() as conn:
        row = conn.execute(
            "SELECT reward_date FROM daily_rewards WHERE user_id=? ORDER BY reward_date DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["reward_date"] if row else None


def _today():
    import datetime
    return datetime.date.today().isoformat()


def get_or_create_user(user_id, username=None, first_name=None, invited_by=None):
    u = get_user(user_id)
    is_new = False
    if not u:
        create_user(user_id, username, first_name, invited_by)
        is_new = True
        u = get_user(user_id)
        credited = is_new
    else:
        with cursor() as conn:
            conn.execute("UPDATE users SET username=?, first_name=? WHERE id=?",
                         (username, first_name, user_id))
        credited = False
    return u, (credited, is_new)


def add_points(user_id, points):
    with cursor() as conn:
        conn.execute("UPDATE users SET points = points + ? WHERE id=?", (points, user_id))
        row = conn.execute("SELECT points FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["points"] >= VIP_THRESHOLD:
            conn.execute("UPDATE users SET is_vip = 1 WHERE id=?", (user_id,))


def is_vip(user_id):
    u = get_user(user_id)
    return bool(u and u["is_vip"])


def add_download_stats(user_id, kind, title=None, filesize=0):
    with cursor() as conn:
        if kind == "audio":
            conn.execute("UPDATE users SET audio_downloads = audio_downloads + 1, total_downloads = total_downloads + 1 WHERE id=?", (user_id,))
        else:
            conn.execute("UPDATE users SET video_downloads = video_downloads + 1, total_downloads = total_downloads + 1 WHERE id=?", (user_id,))
        conn.execute(
            "INSERT INTO downloads (user_id, kind, title, filesize) VALUES (?,?,?,?)",
            (user_id, kind, title or "تحميل", filesize),
        )


def recent_downloads(user_id, limit=10):
    with cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM downloads WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def user_daily_counts(user_id):
    with cursor() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN kind='audio' THEN 1 ELSE 0 END) as audio,
                SUM(CASE WHEN kind='video' THEN 1 ELSE 0 END) as video
            FROM downloads WHERE user_id=? AND created_at >= datetime('now','localtime','-1 day')
        """, (user_id,)).fetchone()
    return {
        "total": row["total"] or 0,
        "audio": row["audio"] or 0,
        "video": row["video"] or 0,
    }


def create_gift_link(owner_id, max_uses, points=GIFT_POINTS):
    import secrets
    code = secrets.token_hex(4)
    with cursor() as conn:
        conn.execute(
            "INSERT INTO gift_links (code, owner_id, max_uses, points) VALUES (?,?,?,?)",
            (code, owner_id, max_uses, points),
        )
    return code


def redeem_gift_link(code, user_id):
    with cursor() as conn:
        row = conn.execute("SELECT * FROM gift_links WHERE code=?", (code,)).fetchone()
        if not row:
            return False, 0, "الرابط غير صحيح."
        if not row["active"]:
            return False, 0, "هذا الرابط معطّل."
        if row["used"] >= row["max_uses"]:
            return False, 0, "هذا الرابط انتهت صلاحيته (اكتمل عدد الاستخدامات)."
        already = conn.execute(
            "SELECT 1 FROM gift_uses WHERE link_id=? AND user_id=?",
            (row["id"], user_id),
        ).fetchone()
        if already:
            return False, 0, "لقد استخدمت هذا الرابط من قبل."
        conn.execute("UPDATE gift_links SET used = used + 1 WHERE id=?", (row["id"],))
        if row["used"] + 1 >= row["max_uses"]:
            conn.execute("UPDATE gift_links SET active=0 WHERE id=?", (row["id"],))
        conn.execute("INSERT INTO gift_uses (link_id, user_id) VALUES (?,?)", (row["id"], user_id))
    add_points(user_id, row["points"])
    return True, row["points"], "تم تفعيل الهدية بنجاح!"


def get_setting(key, default=""):
    with cursor() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with cursor() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))


def daily_download_count(user_id, kind=None):
    with cursor() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as c FROM downloads
            WHERE user_id=? AND created_at >= datetime('now', 'localtime', '-1 day')
        """, (user_id,)).fetchone()
        return row["c"]


def all_users():
    with cursor() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY total_downloads DESC").fetchall()
        return [dict(r) for r in rows]


def total_stats():
    with cursor() as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        downloads = conn.execute("SELECT COUNT(*) c FROM downloads").fetchone()["c"]
        audio = conn.execute("SELECT COUNT(*) c FROM downloads WHERE kind='audio'").fetchone()["c"]
        video = conn.execute("SELECT COUNT(*) c FROM downloads WHERE kind='video'").fetchone()["c"]
        vips = conn.execute("SELECT COUNT(*) c FROM users WHERE is_vip=1").fetchone()["c"]
    return {"users": users, "downloads": downloads, "audio": audio, "video": video, "vips": vips}


def set_vip(user_id, status):
    with cursor() as conn:
        conn.execute("UPDATE users SET is_vip=? WHERE id=?", (1 if status else 0, user_id))


def set_banned(user_id, status):
    with cursor() as conn:
        conn.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if status else 0, user_id))


# ---- حدود التحميل اليومية: روابط / بحث ----
def usage_today(user_id, usage_type):
    with cursor() as conn:
        row = conn.execute(
            "SELECT count FROM daily_usage WHERE user_id=? AND usage_date=date('now','localtime') AND usage_type=?",
            (user_id, usage_type),
        ).fetchone()
        return row["count"] if row else 0


def increment_usage(user_id, usage_type):
    with cursor() as conn:
        conn.execute("""
            INSERT INTO daily_usage (user_id, usage_date, usage_type, count)
            VALUES (?, date('now','localtime'), ?, 1)
            ON CONFLICT(user_id, usage_date, usage_type)
            DO UPDATE SET count = count + 1
        """, (user_id, usage_type))


def usage_limit(uid, usage_type):
    u = get_user(uid)
    is_vip = bool(u and u["is_vip"])
    prefix = f"daily_{usage_type}_limit"
    key = f"{prefix}_vip" if is_vip else f"{prefix}_free"
    default = {"link": "5", "search": "5"}[usage_type]
    return int(get_setting(key, default))


def can_use(uid, usage_type):
    if uid == OWNER_ID:
        return True, ""
    limit = usage_limit(uid, usage_type)
    used = usage_today(uid, usage_type)
    if used >= limit:
        label = "روابط" if usage_type == "link" else "بحث بالاسم"
        return False, f"📊 وصلت للحد اليومي ({limit} {label}).\nارجع غداً أو ترقى إلى VIP 👑 لحد أعلى."
    return True, ""


def consume_usage(uid, usage_type):
    ok, msg = can_use(uid, usage_type)
    if not ok:
        return False, msg
    increment_usage(uid, usage_type)
    return True, ""


# ---- تقييد السرعة ----
def _normalize_url(url):
    u = (url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    return u.lower()


def set_rate_ban(user_id, seconds=1800):
    until = __import__("time").time() + seconds
    with cursor() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rate_bans (user_id, until) VALUES (?,?)", (user_id, until)
        )
    return until


def clear_rate_ban(user_id):
    with cursor() as conn:
        conn.execute("DELETE FROM rate_bans WHERE user_id=?", (user_id,))


def get_rate_ban(user_id):
    if user_id == OWNER_ID:
        return 0
    with cursor() as conn:
        row = conn.execute("SELECT until FROM rate_bans WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return 0
    left = row["until"] - __import__("time").time()
    if left <= 0:
        clear_rate_ban(user_id)
        return 0
    return int(left)


# ---- تفضيلات المستخدمين ----
def set_fact_category(user_id, category):
    with cursor() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_prefs (user_id, fact_category) VALUES (?,?)",
            (user_id, category),
        )


def get_fact_welcomed(user_id):
    with cursor() as conn:
        row = conn.execute(
            "SELECT welcomed FROM user_prefs WHERE user_id=?", (user_id,)
        ).fetchone()
    return bool(row and row["welcomed"])


def mark_fact_welcomed(user_id):
    with cursor() as conn:
        conn.execute("UPDATE user_prefs SET welcomed=1 WHERE user_id=?", (user_id,))


def get_fact_category(user_id):
    with cursor() as conn:
        row = conn.execute(
            "SELECT fact_category FROM user_prefs WHERE user_id=?", (user_id,)
        ).fetchone()
    return row["fact_category"] if row else "both"


def get_fact_offset(user_id, category):
    with cursor() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (f"fact_offset_{category}_{user_id}",)
        ).fetchone()
    try:
        return int(row["value"]) if row and row["value"] else 0
    except (TypeError, ValueError):
        return 0


def set_fact_offset(user_id, category, idx):
    set_setting(f"fact_offset_{category}_{user_id}", str(idx))


# ---- قنوات الاشتراك الإجباري (متعددة) ----
import json


def get_subscription_channels():
    raw = get_setting("subscription_channels", "")
    if not raw:
        cid = get_setting("channel_id", "")
        if cid:
            return [{
                "id": cid,
                "name": get_setting("channel_name", ""),
                "url": get_setting("channel_url", ""),
            }]
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def add_subscription_channel(chat_id, title, url=""):
    channels = get_subscription_channels()
    for ch in channels:
        if ch["id"] == chat_id:
            return False, "هذه القناة مضافة مسبقاً!"
    channels.append({"id": chat_id, "name": title, "url": url})
    set_setting("subscription_channels", json.dumps(channels))
    if len(channels) == 1:
        set_setting("channel_id", channels[0]["id"])
        set_setting("channel_name", channels[0]["name"])
        set_setting("channel_url", channels[0]["url"])
    return True, f"✅ تمت إضافة القناة: {title}"


def remove_subscription_channel(chat_id):
    channels = get_subscription_channels()
    new_channels = [ch for ch in channels if ch["id"] != chat_id]
    if len(new_channels) == len(channels):
        return False, "هذه القناة غير موجودة في القائمة!"
    set_setting("subscription_channels", json.dumps(new_channels))
    if new_channels:
        set_setting("channel_id", new_channels[0]["id"])
        set_setting("channel_name", new_channels[0]["name"])
        set_setting("channel_url", new_channels[0]["url"])
    else:
        set_setting("channel_id", "")
        set_setting("channel_name", "")
        set_setting("channel_url", "")
    return True, "✅ تمت إزالة القناة."


def remove_all_subscription_channels():
    set_setting("subscription_channels", json.dumps([]))
    set_setting("channel_id", "")
    set_setting("channel_name", "")
    set_setting("channel_url", "")
