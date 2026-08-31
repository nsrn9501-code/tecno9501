"""/start — ترحيب، إنشاء المستخدم، إشعار دخول للمالك، روابط الدعوة والهدية."""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import db
from ..config import OWNER_ID, POINTS_PER_REFERRAL
from .system import esc, home_text, main_keyboard, persistent_keyboard

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("🆕 /start من %s", user.id)
    invited_by = None
    gift_code = None
    gift_points = None

    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                invited_by = int(arg[4:])
            except ValueError:
                invited_by = None
        elif arg.startswith("gift_"):
            parts = arg.split("_")
            if len(parts) == 2:
                gift_code = parts[1]

    u, (credited, is_new) = db.get_or_create_user(user.id, user.username, user.first_name, invited_by)
    if u["is_banned"]:
        await update.message.reply_text("⛔ أنت محظور من استخدام هذا البوت.")
        return

    # تفعيل رابط الهدية إن وُجد
    if gift_code:
        ok, pts, msg = db.redeem_gift_link(gift_code, user.id)
        await update.message.reply_text(
            (f"🎁 <b>{msg}</b>\nنقاطك الآن: <b>{db.get_user(user.id)['points']}</b> 🎉" if ok
             else f"❌ {msg}"),
            parse_mode=ParseMode.HTML,
        )

    if credited and invited_by and invited_by != user.id:
        try:
            await context.bot.send_message(
                invited_by,
                f"🎉 أحد أصدقائك إشترك بإستخدام رابط دعوتك! +{POINTS_PER_REFERRAL} نقاط",
            )
        except Exception:
            pass

    # إشعار دخول جديد للمالك
    if is_new:
        try:
            uid = user.id
            uname = user.username or "—"
            fname = user.first_name or "—"
            source = f"🔗 من رابط دعوة <code>{invited_by}</code>" if invited_by else "🎯 مباشرة"
            await context.bot.send_message(
                OWNER_ID,
                f"🆕 <b>مستخدم جديد دخل البوت!</b>\n"
                f"🆔 المعرف: <code>{uid}</code>\n"
                f"📛 الإسم: {esc(fname)}\n"
                f"👤 اليوزر: @{esc(uname)}\n"
                f"{source}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    if is_new:
        # أول دخول: يسأل المستخدم عن نوع المعلومات التي يريدها بعد كل تحميل
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🕌 معلومات دينية", callback_data="factcat:religious"),
                    InlineKeyboardButton("🌍 معلومات عامة", callback_data="factcat:general"),
                ],
                [InlineKeyboardButton("✨ متنوعة (الأثنين معاً)", callback_data="factcat:both")],
            ]
        )
        await update.message.reply_text(
            "👋 أهلاً صديقي!🖤\n\n"
            "أنا بوت تحميل الوسائط 📥\n"
            "وسأرسل لك معلومة مفيدة بعد كل تحميل 💡\n\n"
            "اختر نوع المعلومات الذي يعجبك 👇",
            reply_markup=kb,
        )
        return

    await update.message.reply_text(
        home_text(u),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(user.id),
    )
    await update.message.reply_text(
        "🏠اضغط الزر بالأسفل للعودة للقائمة في أي وقت",
        reply_markup=persistent_keyboard(),
    )
