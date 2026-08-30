"""دوال مساعدة مشتركة + أزرار النظام الرئيسية. تُستخدم من كافة الوحدات."""
import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import db
from ..config import OWNER_ID, VIP_THRESHOLD
from ..state import _OWNER_STATE


def _hide_all():
    """لم تعد هناك لوحة دائمة (تم تحويلها لـ InlineKeyboard) —
    نعيد None لإزالة أي كيبورد قديم متبقي."""
    from telegram import ReplyKeyboardRemove
    return ReplyKeyboardRemove()


def esc(text):
    return html.escape(str(text))


def fmt_duration(sec):
    if not sec:
        return "?"
    sec = int(sec)
    m, s = divmod(sec, 60)
    return f"{m}:{s:02d}"


def main_keyboard(user_id):
    """لوحة الأزرار النظامية (InlineKeyboard) — تختفي تلقائياً عند الرجوع/التمرير."""
    rows = [
        [InlineKeyboardButton("⬇️ تحميل/بحث", callback_data="main:download"),
         InlineKeyboardButton("📊 حسابي", callback_data="main:stats")],
        [InlineKeyboardButton("✨ مكافأة يومية", callback_data="main:daily"),
         InlineKeyboardButton("🔗 رابط الدعوة", callback_data="main:referral")],
        [InlineKeyboardButton("💎 نظام الـ VIP", callback_data="main:vip"),
         InlineKeyboardButton("💬 كروب المناقشة", callback_data="main:discussion")],
        [InlineKeyboardButton("🧠 معلومتي", callback_data="main:fact"),
         InlineKeyboardButton("🧭 المساعدة", callback_data="main:help")],
        [InlineKeyboardButton("المطور", callback_data="main:owner")],
    ]
    return InlineKeyboardMarkup(rows)


def home_text(user):
    return (
        "أهلاً صديقي! ✨\n\n"
        "أنا بوت تحميل الوسائط 🚀\n\n"
        "📎 أرسل لي رابطاً من:\n"
        "   ▶️ YouTube\n"
        "   📸 Instagram\n"
        "   🎵 TikTok\n"
        "   📘 Facebook\n\n"
        "🔍 أو أرسل اسم أغنية / مقطع وسأبحث لك على يوتيوب 🎶\n\n"
        "استخدم الأزرار بالأسفل 👇"
    )


def owner_card():
    """بطاقة المالك تظهر للمستخدمين عند ضغط زر لوحة المالك."""
    u = db.get_user(OWNER_ID) or {}
    name = esc(u.get("first_name") or "المالك")
    uname = u.get("username") or "N_S_R01"
    txt = (
        "👑 <b>لوحة المطور</b>\n\n"
        f"👑 المطور: @{esc(uname)}\n"
        "لأي مشكلة أو اقتراح راسله مباشرة من الزر بالأسفل 👇"
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✉️ راسل المطور", url=f"https://t.me/{uname}")]]
    )
    return txt, kb


def vip_bar(points):
    """شريط تقدم VIP بالنمط █████░."""
    total = VIP_THRESHOLD
    filled = max(0, min(total, points))
    blocks = 10
    filled_blocks = round(filled / total * blocks)
    return "█" * filled_blocks + "░" * (blocks - filled_blocks)


def stats_text(u, daily):
    vip = "✅" if u["is_vip"] else "❌"
    bar = vip_bar(u["points"])
    lines = [
        "👤 <b>حسابك</b>",
        f"🆔 المعرف: <code>{u['id']}</code>",
        f"👤 اليوزر: @{esc(u['username'] or '—')}",
        f"⭐ النقاط: <b>{u['points']}</b>",
        f"👑 VIP: {vip}",
        f"   تقدم: {bar}  <code>{u['points']}/{VIP_THRESHOLD}</code>",
        "",
        f"📥 إجمالي التحميلات: <b>{u['total_downloads']}</b>",
        f"🎵 صوتيات: {u['audio_downloads']}  🎬 فيديو: {u['video_downloads']}",
        f"👥 عدد دعواتك: {u['referrals']}",
        f"🗓 تحميلات اليوم: {daily['total']} (صوتي {daily['audio']} / فيديو {daily['video']})",
        "",
        "📊 <b>حدود اليوم:</b>",
        f"🔗 روابط: {db.usage_today(u['id'], 'link')}/{db.usage_limit(u['id'], 'link')}",
        f"🔍 بحث: {db.usage_today(u['id'], 'search')}/{db.usage_limit(u['id'], 'search')}",
    ]
    return "\n".join(lines)


async def send_stats_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = db.get_user(update.effective_user.id)
    if not u:
        return
    daily = db.user_daily_counts(u["id"])
    text = stats_text(u, daily)
    recent = db.recent_downloads(u["id"], 5)
    if recent:
        text += "\n\n🕓 <b>آخر تحميلاتك:</b>"
        for r in recent:
            kind = "🎵" if r["kind"] == "audio" else "🎬"
            text += f"\n{kind} {esc(r['title'] or '—')}"
    msg = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return msg


async def send_stats_message(bot, chat_id, user_id):
    u = db.get_user(user_id)
    if not u:
        return
    daily = db.user_daily_counts(user_id)
    text = stats_text(u, daily)
    recent = db.recent_downloads(user_id, 5)
    if recent:
        text += "\n\n🕓 <b>آخر تحميلاتك:</b>"
        for r in recent:
            kind = "🎵" if r["kind"] == "audio" else "🎬"
            text += f"\n{kind} {esc(r['title'] or '—')}"
    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)


def looks_like_url(text):
    from urllib.parse import urlparse
    parsed = urlparse(text.strip())
    return parsed.scheme in ("http", "https") and "." in parsed.netloc


def set_owner_state(uid, state):
    _OWNER_STATE[uid] = state


def get_owner_state(uid):
    return _OWNER_STATE.get(uid)


def clear_owner_state(uid):
    _OWNER_STATE.pop(uid, None)
