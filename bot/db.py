import sqlite3
from contextlib import contextmanager

from .config import DAILY_REWARD_POINTS, DB_PATH, GIFT_POINTS, OWNER_ID, POINTS_PER_REFERRAL, VIP_THRESHOLD


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def cursor():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


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
        # migration: add downloads detail columns if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(downloads)").fetchall()]
        if "title" not in cols:
            conn.execute("ALTER TABLE downloads ADD COLUMN title TEXT")
        if "filesize" not in cols:
            conn.execute("ALTER TABLE downloads ADD COLUMN filesize INTEGER")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('gift_default_points', '10')")
        # Ensure default settings
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_id', '')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_name', '')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_url', '')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_limit_free', '3')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_limit_vip', '20')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_link_limit_free', '5')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_link_limit_vip', '15')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_search_limit_free', '5')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_search_limit_vip', '15')")


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
    """يضيف مكافأة يومية مرة واحدة فقط في اليوم. يعيد True إن نجح."""
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
        # refresh username/name on interaction
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
    """يعيد (نجاح، نقاط، رسالة) عند استعمال رابط هدية."""
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


# ---- تقييد السرعة (3 روابط/دقيقة أو تكرار نفس الرابط) ----
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
    """يعيد (متبقي_ثواني) أو 0 إن لم يكن مقيّداً."""
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
