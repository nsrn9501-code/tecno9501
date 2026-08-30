"""معالجة الـ inline callbacks + أمر الإلغاء."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import db
from ..config import OWNER_ID
from ..jobs import schedule_download
from ..state import _OWNER_STATE, _PENDING_LINKS, _SEARCH_RESULTS, _USER_BUSY
from .owner import owner_cb
from .subscription import check_limits, sub_status
from .system import esc


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    data = q.data or ""
    chat_id = update.effective_chat.id

    if data.startswith("main:"):
        await _main_menu_cb(update, context, data[5:], uid, chat_id)
        return

    if data == "cancel":
        _SEARCH_RESULTS.pop(uid, None)
        await q.edit_message_text("🚫 تم الإلغاء.")
        return

    if data.startswith("own:"):
        if uid != OWNER_ID:
            await q.answer("⛔ غير مصرح لك")
            return
        await owner_cb(q, context, data[4:], uid, chat_id)
        return

    if data.startswith("factcat:"):
        cat = data.split(":", 1)[1]
        if cat in ("religious", "general", "both"):
            db.set_fact_category(uid, cat)
        labels = {"religious": "دينية 🕌", "general": "عامة 🌍", "both": "متنوعة ✨"}
        await q.edit_message_text(
            f"💡 تم حفظ اختيارك: <b>{labels.get(cat, 'متنوعة ✨')}</b>\n"
            "ستصلك معلومة جديدة بعد كل تحميل إن شاء الله 🎁",
            parse_mode="HTML",
        )
        if not db.get_fact_welcomed(uid):
            db.mark_fact_welcomed(uid)
            u = db.get_user(uid) or {}
            from .system import home_text, main_keyboard
            await context.bot.send_message(
                chat_id,
                home_text(u),
                parse_mode="HTML",
                reply_markup=main_keyboard(uid),
            )
        return

    if data == "verify:sub":
        status = await sub_status(context.bot, uid)
        if status == "ok":
            await q.edit_message_text(
                "✅ تم التحقق بنجاح! أرسل رابطاً للتحميل أو إسم أغنية للبحث.",
            )
        else:
            await q.answer("❌ لست مشتركاً بعد!", show_alert=True)
        return

    if data.startswith("qual:"):
        # جودة مختارة — نبدأ التحميل بالجودة المختارة
        if uid in _PENDING_LINKS:
            link = _PENDING_LINKS.pop(uid)
            height_str = data.split(":", 1)[1]
            if height_str == "auto":
                kind_sel = "video"
                label = "تلقائي ⚡"
                max_h = None
            elif height_str == "0":
                kind_sel = "audio"
                label = "صوت MP3 🎵"
                max_h = None
            else:
                kind_sel = "video"
                max_h = int(height_str)
                label = f"{max_h}p"
            await q.edit_message_text(
                f"⏳ جاري التحميل ({label})…", parse_mode=ParseMode.HTML
            )
            await schedule_download(
                bot=context.bot, chat_id=chat_id, uid=uid, url=link["url"],
                platform=link["platform"], kind=kind_sel,
                status_id=q.message.message_id, max_height=max_h,
            )
        else:
            await q.edit_message_text("⏰ انتهت صلاحية الاختيار، أعد إرسال الرابط.")
        return

    if data.startswith("pick:"):
        idx = int(data.split(":")[1])
        results = _SEARCH_RESULTS.get(uid)
        if not results or idx >= len(results):
            await q.edit_message_text("😕 انتهت صلاحية البحث، أعد المحاولة.")
            return
        r = results[idx]
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🎵 MP3 صوت", callback_data=f"fmt:a:{idx}"),
                    InlineKeyboardButton("🎬 MP4 فيديو", callback_data=f"fmt:v:{idx}"),
                ],
                [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")],
            ]
        )
        await q.edit_message_text(
            f"📥 <b>{esc(r['title'])}</b>\nاختر الصيغة:", parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    if data.startswith("fmt:"):
        _, kind, idx = data.split(":")
        idx = int(idx)
        results = _SEARCH_RESULTS.get(uid)
        if not results or idx >= len(results):
            await q.edit_message_text("😕 انتهت صلاحية البحث، أعد المحاولة.")
            return
        r = results[idx]
        url = f"https://www.youtube.com/watch?v={r['id']}"
        await q.edit_message_text(
            f"⏳ جاري تجهيز «{esc(r['title'])}»…\nتحميل {('صوتي 🎵' if kind=='a' else 'فيديو 🎬')}",
            parse_mode=ParseMode.HTML,
        )
        _SEARCH_RESULTS.pop(uid, None)

        if _USER_BUSY.get(uid):
            await context.bot.send_message(chat_id, "⏳ عندك طلب قيد التنفيذ، إنتظر رجاءً…")
            return

        await schedule_download(
            bot=context.bot, chat_id=chat_id, uid=uid, url=url,
            platform="youtube", kind=("audio" if kind == "a" else "video"),
            status_id=q.message.message_id,
        )
        return


async def _main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, uid: int, chat_id: int):
    """معالجة أزرار القائمة الرئيسية (InlineKeyboard) — ترسل رداً جديداً. """
    from .system import esc, owner_card, send_stats_message, vip_bar
    from ..config import (
        DAILY_REWARD_POINTS, POINTS_PER_AUDIO, POINTS_PER_REFERRAL,
        POINTS_PER_VIDEO, VIP_THRESHOLD,
    )
    first = update.effective_user.first_name
    uname = update.effective_user.username

    if action == "download":
        await context.bot.send_message(
            chat_id,
            "⬇️ <b>تحميل / بحث</b>\n"
            "أرسل رابط فيديو مباشرة للتحميل، أو اكتب إسم أغنية/مقطع وسأبحث لك على يوتيوب.\n\n"
            "✦ <b>الروابط المدعومة:</b>\n"
            "   YouTube ✅\n   Instagram ✅\n   TikTok ✅\n   Facebook ✅",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "stats":
        await send_stats_message(context.bot, chat_id, uid)
        return

    if action == "daily":
        db.get_or_create_user(uid, uname, first)
        if db.claim_daily_reward(uid):
            await context.bot.send_message(
                chat_id,
                f"🎁 <b>مبروك!</b> حصلت على مكافأة اليوم +{DAILY_REWARD_POINTS} نقطة 🎉\n"
                f"نقاطك الآن: <b>{db.get_user(uid)['points']}</b>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await context.bot.send_message(
                chat_id,
                "⏳ لقد أخذت مكافأة اليوم مسبقاً.\n"
                "ارجع غداً لتحصل على نقاط إضافية 🎁",
            )
        return

    if action == "referral":
        db.get_or_create_user(uid, uname, first)
        username = context.bot_data.get("username", "YourBot")
        link = f"https://t.me/{username}?start=ref_{uid}"
        txt = (
            "🔗 <b>رابط دعوتك</b>\n\n"
            f"<code>{link}</code>\n\n"
            "شارك الرابط مع أصدقائك 👇\n"
            f"• عندما يدخل صديق من رابطك تحصل على <b>+{POINTS_PER_REFERRAL} نقاط</b>\n"
            "• كلما زادت نقاطك اقتربت من رتبة <b>VIP</b> 👑"
        )
        await context.bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML)
        return

    if action == "vip":
        u, _ = db.get_or_create_user(uid, uname, first)
        vip_txt = "✅ أنت عضو VIP 👑" if u["is_vip"] else "❌ لست VIP بعد"
        bar = vip_bar(u["points"])
        txt = (
            "💎 <b>نظام الـ VIP</b>\n\n"
            "💠 <b>كيف تصير VIP؟</b>\n"
            f"• تحميل فيديو ناجح = +{POINTS_PER_VIDEO} نقاط\n"
            f"• تحميل أغنية ناجحة = +{POINTS_PER_AUDIO} نقاط\n"
            f"• كل صديق تدعوه ويستخدم البوت = +{POINTS_PER_REFERRAL} نقاط\n"
            f"• المكافأة اليومية = +{DAILY_REWARD_POINTS} نقاط\n"
            f"• عند وصولك <b>{VIP_THRESHOLD} نقطة</b> تصبح VIP تلقائياً 🎉\n\n"
            "👑 <b>مميزات VIP:</b>\n"
            "✅ استخدام البوت بدون اشتراك إجباري\n"
            "✅ سرعة تحميل أعلى (أولوية في الطابور)\n"
            "✅ تحميل بجودة تصل إلى 1080p\n"
            "✅ حدود تحميل يومية أعلى\n\n"
            f"📊 <b>نقاطك الحالية:</b> {u['points']}\n"
            f"{bar}  <code>{u['points']}/{VIP_THRESHOLD}</code>\n"
            f"👑 الحالة: {vip_txt}"
        )
        await context.bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML)
        return

    if action == "discussion":
        link = db.get_setting("discussion_group")
        enabled = db.get_setting("group_enabled", "0") == "1"
        if enabled and link:
            await context.bot.send_message(
                chat_id,
                "💬 <b>كروب المناقشة</b>\n\n"
                "انضم إلينا وتواصل مع الأعضاء والإدارة 👇\n"
                f"{link}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await context.bot.send_message(
                chat_id,
                "💬 لم يتم تعيين كروب مناقشة بعد، حاول لاحقاً.",
            )
        return

    if action == "fact":
        cur = db.get_fact_category(uid)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🕌 دينية", callback_data="factcat:religious"),
                    InlineKeyboardButton("🌍 عامة", callback_data="factcat:general"),
                ],
                [InlineKeyboardButton("✨ متنوعة (تناوب)", callback_data="factcat:both")],
            ]
        )
        labels = {"religious": "دينية 🕌", "general": "عامة 🌍", "both": "متنوعة ✨"}
        await context.bot.send_message(
            chat_id,
            "💡 <b>المعلومات بعد كل تحميل</b>\n\n"
            "بعد كل فيديو أو أغنية تنزّلها سأرسل لك معلومة قصيرة.\n"
            "اختر نوع المعلومات الذي يعجبك 👇\n\n"
            f"الحالي: <b>{labels.get(cur, 'متنوعة ✨')}</b>",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if action == "help":
        txt = (
            "🧭 <b>تعليمات البوت</b>\n\n"
            "📎 <b>تحميل من رابط:</b>\n"
            "أرسل رابط فيديو من يوتيوب / انستغرام / تيك توك / فيسبوك وسأرسله لك.\n\n"
            "🔍 <b>بحث على يوتيوب:</b>\n"
            "اكتب إسم الأغنية أو المقطع وستظهر لك النتائج، اختر ثم أختر صوت MP3 أو فيديو MP4.\n\n"
            "📊 <b>حدود اليوم (لغير VIP):</b>\n"
            "🔗 5 تحميلات عبر الروابط\n"
            "🔍 5 تحميلات عبر البحث بالاسم\n"
            "⏱ إرسال أكثر من 3 روابط خلال دقيقة أو تكرار نفس الرابط = تقييد 30 دقيقة.\n\n"
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
            "مطلوب الإشتراك في قناة البوت لإستخدامه (باستثناء VIP)."
        )
        await context.bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML)
        return

    if action == "owner":
        if uid == OWNER_ID:
            from .owner import _send_owner_panel_msg
            await _send_owner_panel_msg(context.bot, chat_id)
        else:
            txt, kb = owner_card()
            await context.bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in _OWNER_STATE:
        del _OWNER_STATE[uid]
        await update.message.reply_text("🚫 تم الإلغاء.")
    else:
        await update.message.reply_text("لا يوجد إجراء قيد التنفيذ.")
