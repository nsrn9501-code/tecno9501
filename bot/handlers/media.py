"""معالجة النصوص: روابط، بحث يوتيوب، أزرار النظام."""
import asyncio
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import db, downloader
from ..facts import next_fact
from ..config import OWNER_ID
from ..jobs import schedule_download
from ..state import _PENDING_LINKS, _RATE_URLS, _SEARCH_RESULTS, _USER_BUSY
from .system import (
    esc,
    fmt_duration,
    get_owner_state,
    looks_like_url,
    main_keyboard,
    owner_card,
    send_stats_reply,
    vip_bar,
)
from ..config import (
    DAILY_REWARD_POINTS,
    POINTS_PER_AUDIO,
    POINTS_PER_REFERRAL,
    POINTS_PER_VIDEO,
    RATE_BAN_SECONDS,
    RATE_MAX_LINKS,
    RATE_WINDOW_SECONDS,
    VIP_THRESHOLD,
)

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id
    logger.info("📥 رسالة من %s: %s", uid, text[:80])

    # أزرار النظام
    if text == "👤 حسابي":
        await send_stats_reply(update, context)
        return
    if text in ("📥 تحميل/بحث", "تحميل/بحث", "📥"):
        await update.message.reply_text(
            "📥 <b>تحميل / بحث</b>\n"
            "أرسل رابط فيديو مباشرة للتحميل، أو اكتب إسم أغنية/مقطع وسأبحث لك على يوتيوب.\n\n"
            "✦ <b>الروابط المدعومة:</b> YouTube • Instagram • TikTok • Facebook",
            parse_mode=ParseMode.HTML,
        )
        return
    if text in ("ℹ️ المساعدة", "مساعدة", "ℹ️"):
        from .help import cmd_help
        await cmd_help(update, context)
        return
    if text in ("🎁 مكافأة يومية", "مكافأة", "🎁"):
        from .help import cmd_daily
        await cmd_daily(update, context)
        return
    if text in ("🔗 رابط الدعوة", "دعوة", "🔗"):
        from .help import cmd_referral
        await cmd_referral(update, context)
        return
    if text in ("⭐ نظام الـ VIP", "نظام الـ VIP", "VIP", "⭐"):
        await _vip_card(update, context)
        return
    if text in ("💬 كروب المناقشة", "كروب المناقشة", "كروب", "💬"):
        await _discussion_group(update, context)
        return
    if text in ("💡 معلومتي", "معلومتي", "💡"):
        await _my_fact(update, context)
        return
    if text in ("🙈 إخفاء الأزرار", "إخفاء الأزرار", "🙈"):
        return  # تم إلغاء الزر
    if text == "🏠 رجوع":
        return  # تم إلغاء الزر
    if text in ("👑 المطور", "👑 لوحة المطور"):
        if uid == OWNER_ID:
            from .owner import cmd_owner
            await cmd_owner(update, context)
        else:
            txt, kb = owner_card()
            await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # حالة إدخال المالك
    if uid == OWNER_ID and get_owner_state(uid):
        from .owner import handle_owner_input
        await handle_owner_input(update, context)
        return

    u, _ = db.get_or_create_user(uid, user.username, user.first_name)
    if u["is_banned"]:
        await update.message.reply_text("⛔ أنت محظور من استخدام هذا البوت.")
        return

    # اشتراك إجباري
    from .subscription import check_limits, join_prompt, sub_status
    status = await sub_status(context.bot, uid)
    if status != "ok":
        await join_prompt(context.bot, uid, chat_id)
        return

    # تقييد مؤقت 30 دقيقة لمن أرسل روابط كثيرة/مكررة
    left = db.get_rate_ban(uid)
    if left > 0:
        mins = (left // 60) + 1
        await update.message.reply_text(
            f"⏸️ أنت مقيّد مؤقتاً بسبب إرسال روابط متكررة أو سريعة.\n"
            f"🔇 التقييد سينتهي خلال <b>~{mins} دقيقة</b>.\n"
            "💡 انتظر حتى تنتهي المدة ثم حاول مجدداً.",
            parse_mode="HTML",
        )
        return

    if _USER_BUSY.get(uid):
        await update.message.reply_text("⏳ عندك طلب تحميل قيد التنفيذ، إنتظر رجاءً…")
        return

    if looks_like_url(text):
        platform = downloader.detect_platform(text)
        if not platform:
            await update.message.reply_text(
                "🤔 الرابط غير مدعوم. المدعوم حالياً:\nYouTube • Instagram • TikTok • Facebook\n\n"
                "أو اكتب إسم أغنية للبحث على يوتيوب."
            )
            return

        # 1) حد الروابط اليومي (5)
        ok, msg = db.consume_usage(uid, "link")
        if not ok:
            await update.message.reply_text(msg)
            return

        # 2) كشف الغش: 3 روابط خلال دقيقة أو إعادة نفس الرابط (الماالك معفى)
        if uid != OWNER_ID:
            now = time.time()
            norm = db._normalize_url(text)
            rec = _RATE_URLS.setdefault(uid, {"times": [], "recent_urls": {}})
            rec["times"] = [t for t in rec["times"] if now - t <= RATE_WINDOW_SECONDS]
            rec["times"].append(now)
            prev_dup = rec["recent_urls"].get(norm)
            rec["recent_urls"] = {u: t for u, t in rec["recent_urls"].items() if now - t <= RATE_WINDOW_SECONDS}
            rec["recent_urls"][norm] = now
            if prev_dup is not None:
                db.set_rate_ban(uid, RATE_BAN_SECONDS)
                await update.message.reply_text(
                    "⛔ <b>تم تقييدك مؤقتاً لمدة 30 دقيقة!</b>\n"
                    "🔄 أرسلت نفس الرابط أكثر من مرة، وهذا يُعتبر غشاً في النقاط.\n"
                    "🔇 انتظر انتهاء المدة ثم حاول مجدداً.",
                    parse_mode="HTML",
                )
                return
            if len(rec["times"]) >= RATE_MAX_LINKS:
                db.set_rate_ban(uid, RATE_BAN_SECONDS)
                await update.message.reply_text(
                    "⛔ <b>تم تقييدك مؤقتاً لمدة 30 دقيقة!</b>\n"
                    "🚀 أرسلت أكثر من 3 روابط خلال دقيقة واحدة.\n"
                    "🔇 انتظر انتهاء المدة ثم حاول مجدداً.",
                    parse_mode="HTML",
                )
                return

        # نقترح الجودة المتاحة قبل بدء التحميل (Quality Selector)
        await _ask_quality(update, context, text, platform)
    else:
        # حد البحث اليومي (5) — يُستهلك عند إجراء البحث نفسه
        ok, msg = db.consume_usage(uid, "search")
        if not ok:
            await update.message.reply_text(msg)
            return
        await do_search(update, context, text)


async def start_download(update, context, url, platform, kind, max_height=None):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    status_msg = await update.message.reply_text(
        f"✅ تم التعرف على الرابط ({platform}).\n⏳ جاري التحضير…",
    )
    await schedule_download(
        bot=context.bot, chat_id=chat_id, uid=uid, url=url,
        platform=platform, kind=kind, status_id=status_msg.message_id,
        max_height=max_height,
    )


async def _ask_quality(update, context, url, platform):
    """يعرض أزرار الجودة المتاحة قبل التحميل (360p/720p/1080p/MP3)."""
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    status_msg = await update.message.reply_text(
        f"✅ تم التعرف على الرابط ({platform}).\n⏳ جاري فحص الجودات المتاحة…",
    )
    try:
        qualities = await asyncio.to_thread(downloader.fetch_qualities, url)
    except Exception:
        qualities = []
    if not qualities:
        # ما في جودات معروفة — أرسل مباشرة كفيديو
        await start_download(update, context, url, platform, "video")
        return
    _PENDING_LINKS[uid] = {"url": url, "platform": platform, "kind": "video"}
    rows = []
    for i in range(0, len(qualities), 2):
        row = []
        for q in qualities[i:i+2]:
            row.append(InlineKeyboardButton(
                q["label"], callback_data=f"qual:{q['height']}")
            )
        rows.append(row)
    rows.append([
        InlineKeyboardButton("⚡ تلقائي", callback_data="qual:auto"),
        InlineKeyboardButton("❌ إلغاء", callback_data="cancel"),
    ])
    lines = "\n".join(f"• {q['label']}" for q in qualities)
    await status_msg.edit_text(
        f"📥 <b>اختر الجودة:</b>\n{lines}\n\n"
        "⚡ <b>تلقائي</b> = أفضل جودة حسب رتبتك (VIP: 1080p / عادي: 720p)",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _vip_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بطاقة نظام الـ VIP للمستخدمين."""
    uid = update.effective_user.id
    u, _ = db.get_or_create_user(uid, update.effective_user.username, update.effective_user.first_name)
    vip_txt = "✅ أنت عضو VIP 👑" if u["is_vip"] else "❌ لست VIP بعد"
    bar = vip_bar(u["points"])
    txt = (
        "⭐ <b>نظام الـ VIP</b>\n\n"
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
    await update.message.reply_text(
        txt, parse_mode=ParseMode.HTML, reply_markup=_hide_all()
    )


async def _discussion_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر كروب المناقشة: يرسل رابط الكروب إن كان مفعلاً."""
    uid = update.effective_user.id
    link = db.get_setting("discussion_group")
    enabled = db.get_setting("group_enabled", "0") == "1"
    if enabled and link:
        await update.message.reply_text(
            "💬 <b>كروب المناقشة</b>\n\n"
            "انضم إلينا وتواصل مع الأعضاء والإدارة 👇\n"
            f"{link}",
            parse_mode=ParseMode.HTML,
            reply_markup=_hide_all(),
        )
    else:
        await update.message.reply_text(
            "💬 لم يتم تعيين كروب مناقشة بعد، حاول لاحقاً.",
            reply_markup=_hide_all(),
        )


async def _my_fact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر معلومتي: يعرض للمستخدم اختيار نوع المعلومة القادمة."""
    uid = update.effective_user.id
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
    await update.message.reply_text(
        "💡 <b>المعلومات بعد كل تحميل</b>\n\n"
        "بعد كل فيديو أو أغنية تنزّلها سأرسل لك معلومة قصيرة.\n"
        "اختر نوع المعلومات الذي يعجبك 👇\n\n"
        f"الحالي: <b>{labels.get(cur, 'متنوعة ✨')}</b>",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def _hide_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر إخفاء الأزرار: يخفي الأزرار ويترك زر رجوع واحد."""
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    await update.message.reply_text(
        "🙈 تم إخفاء الأزرار.\n\n"
        "للعودة للقائمة اضغط زر «🏠 رجوع» بالأسفل.",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🏠 رجوع")]],
            resize_keyboard=True, is_persistent=True,
        ),
    )


async def do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    status_msg = await update.message.reply_text(
        f"🔍 جاري البحث عن «<b>{esc(query)}</b>»…", parse_mode=ParseMode.HTML
    )
    try:
        results = await asyncio.to_thread(downloader.search_youtube, query, 5)
    except Exception as exc:
        await status_msg.edit_text(f"❌ فشل البحث: {exc}")
        return
    if not results:
        await status_msg.edit_text("😕 لا توجد نتائج. جرب صياغة أخرى.")
        return
    _SEARCH_RESULTS[uid] = results
    lines = [f"🎯 نتائج البحث عن «{esc(query)}»:", ""]
    kb = []
    for i, r in enumerate(results):
        lines.append(f"{i+1}. {esc(r['title'])} — <i>{fmt_duration(r['duration'])}</i>")
        kb.append([InlineKeyboardButton(f"{i+1}. {r['title'][:40]}", callback_data=f"pick:{i}")])
    kb.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    await status_msg.edit_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb),
    )
