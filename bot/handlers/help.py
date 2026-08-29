"""المساعدة، المكافأة اليومية، رابط الدعوة."""
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import db
from ..config import DAILY_REWARD_POINTS, POINTS_PER_AUDIO, POINTS_PER_REFERRAL, POINTS_PER_VIDEO, VIP_THRESHOLD
from .system import main_keyboard


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "ℹ️ <b>تعليمات البوت</b>\n\n"
        "📎 <b>تحميل من رابط:</b>\n"
        "أرسل رابط فيديو من يوتيوب / انستغرام / تيك توك / فيسبوك وسأرسله لك.\n\n"
        "🔍 <b>بحث على يوتيوب:</b>\n"
        "اكتب إسم الأغنية أو المقطع وستظهر لك النتائج، اختر ثم أختر صوت MP3 أو فيديو MP4.\n\n"
        "👑 <b>نظام النقاط وVIP:</b>\n"
        f"• تحميل فيديو ناجح = +{POINTS_PER_VIDEO} نقاط\n"
        f"• تحميل أغنية ناجحة = +{POINTS_PER_AUDIO} نقاط\n"
        f"• كل صديق دعوته يبدأ بإستخدام البوت = +{POINTS_PER_REFERRAL} نقاط\n"
        f"• المكافأة اليومية = +{DAILY_REWARD_POINTS} نقاط\n"
        f"• عند وصولك <b>{VIP_THRESHOLD} نقطة</b> تصبح عضو <b>VIP</b> تلقائياً 🎉\n\n"
        "🎁 <b>مميزات VIP:</b>\n"
        "✅ لا حاجة للإشتراك الإجباري بالقناة\n"
        "✅ أولوية أعلى في قائمة التحميل (أسرع)\n"
        "✅ تحميل بجودة تصل إلى <b>1080p</b>\n"
        "✅ حدود تحميل يومية أعلى\n\n"
        "📢 <b>اشتراك إجباري:</b>\n"
        "مطلوب الإشتراك في قناة البوت لإستخدامه (باستثناء VIP).\n\n"
        "استخدم الأزرار بالأسفل 👇"
    )
    uid = update.effective_user.id
    await update.message.reply_text(
        txt, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(uid)
    )


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    _, _ = db.get_or_create_user(uid, update.effective_user.username, update.effective_user.first_name)
    if db.claim_daily_reward(uid):
        await update.message.reply_text(
            f"🎁 <b>مبروك!</b> حصلت على مكافأة اليوم +{DAILY_REWARD_POINTS} نقطة 🎉\n"
            f"نقاطك الآن: <b>{db.get_user(uid)['points']}</b>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "⏳ لقد أخذت مكافأة اليوم مسبقاً.\n"
            "ارجع غداً لتحصل على نقاط إضافية 🎁",
        )


async def cmd_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u, _ = db.get_or_create_user(uid, update.effective_user.username, update.effective_user.first_name)
    username = context.bot_data.get("username", "YourBot")
    link = f"https://t.me/{username}?start=ref_{u['id']}"
    txt = (
        "🔗 <b>رابط دعوتك</b>\n\n"
        f"<code>{link}</code>\n\n"
        "شارك الرابط مع أصدقائك 👇\n"
        f"• عندما يدخل صديق من رابطك تحصل على <b>+{POINTS_PER_REFERRAL} نقاط</b>\n"
        "• كلما زادت نقاطك اقتربت من رتبة <b>VIP</b> 👑"
    )
    await update.message.reply_text(
        txt, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(uid)
    )
