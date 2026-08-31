"""طابور التحميل وتنفيذ مهام التنزيل والإرسال."""
import asyncio
import logging
import os
import time
import traceback

from . import uploader

from . import db
from .config import POINTS_PER_AUDIO, POINTS_PER_VIDEO, VIP_THRESHOLD
from .downloader import (DownloadError, cleanup, download_audio, download_video,
                          ensure_telegram_compatible, probe_media)
from .facts import next_fact
from .handlers.system import esc
from . import state
from .state import _USER_BUSY, NUM_WORKERS, get_sem

logger = logging.getLogger(__name__)

def enqueue(user_id, coro):
    state._job_seq += 1
    priority = 0 if db.is_vip(user_id) else 1
    approx_pos = state._job_queue.qsize()
    state.set_queue_position(user_id, approx_pos)
    state._job_queue.put_nowait((priority, state._job_seq, coro))
    logger.info("🎯 أُضيفت مهمة إلى الطابور: user=%s seq=%s", user_id, state._job_seq)

async def queue_worker(app):
    """سلوك: يُشغّل عدة عمال (W​orkers) يعالجون المهام بالتوازي،
    مع سيمافور يحدّ عدد المهام الفعلية المتزامنة حتى لا نُغرق تيليجرام.
    كل عامل يحترم أولوية VIP من صف الأولوية."""
    workers = [asyncio.create_task(_worker(app, i)) for i in range(NUM_WORKERS)]
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        raise


async def _worker(app, idx):
    """عامل واحد: يسحب المهام من صف الأولوية وينفذها (تحت السيمافور)."""
    while True:
        try:
            _prio, _seq, coro = await asyncio.wait_for(state._job_queue.get(), timeout=0.5)
            try:
                # السيمافور يمنع تجاوز عدد المهام المتزامنة الفعلية
                async with get_sem():
                    await coro()
            except Exception:
                logger.error("Job crashed:\n%s", traceback.format_exc())
            finally:
                state._job_queue.task_done()
        except asyncio.CancelledError:
            break
        except asyncio.TimeoutError:
            # لا يوجد مهام في انتظار — نعيد المحاولة
            continue
        except Exception:
            logger.error("Queue error:\n%s", traceback.format_exc())
            await asyncio.sleep(0.5)

def _is_network_error(exc_msg):
    """يتحقق إذا كان الخطأ بسبب مشكلة شبكة مؤقتة (DNS, timeout, connection)."""
    low = str(exc_msg).lower()
    keywords = [
        "failed to resolve", "temporary failure in name resolution",
        "name or service not known", "getaddrinfo failed",
        "connection refused", "connection reset", "connection timed out",
        "timed out", "timeout", "network is unreachable",
        "temporary failure", "errno -3", "errno -2",
        "max retries exceeded", "httpconnectionpool",
        "urllib3.exceptions", "httpx.connecterror", "httpx.connect",
        "transporterror",
    ]
    return any(k in low for k in keywords)


_MAX_RETRIES = 1
_RETRY_DELAY = [3]  # في حال أُعيدت المحاولة (نادراً)


async def _retry_download(download_fn, *args, timeout=150, **kwargs):
    """ينفذ عملية التحميل مع إعادة محاولة تلقائية عند أخطاء الشبكة.
    كل محاولة لها مهلة كاملة خاصة بها بدل مهلة مركزية."""
    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(download_fn, *args, **kwargs), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:
            if _is_network_error(str(exc)) and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAY[min(attempt, len(_RETRY_DELAY)-1)]
                logger.warning(
                    "🔄 خطأ شبكة (محاولة %d/%d): %s — إعادة المحاولة بعد %d ثانية",
                    attempt+1, _MAX_RETRIES, str(exc)[:100], delay
                )
                last_err = exc
                await asyncio.sleep(delay)
            else:
                raise
    raise last_err


async def schedule_download(*, bot, chat_id, uid, url, platform, kind, status_id, max_height=None):
    """جدولة تحميل + رفع للتيليغرام. kind = 'audio' | 'video'."""
    _USER_BUSY[uid] = True
    queue_size = state._job_queue.qsize() if state._job_queue else 0
    if queue_size > 2:
        try:
            await uploader.a_edit_message_text(chat_id, status_id, "The queue is busy (" + str(queue_size) + " jobs ahead)... Loading...")
        except Exception:
            pass
    points = POINTS_PER_VIDEO if kind == "video" else POINTS_PER_AUDIO

    async def do_job():
        try:
            start_time = time.time()
            logger.info("⏳ بدء مهمة تحميل: platform=%s kind=%s", platform, kind)
            user = db.get_user(uid) or {}
            user_disp = (user.get("first_name") or "مستخدم")
            user_uname = user.get("username") or "—"
            # مهل أقصر بكثير للسرعة القصوى
            timeout = 90 if platform in ("instagram", "tiktok", "facebook") else 120
            if kind == "audio":
                path, title = await _retry_download(download_audio, url, timeout=timeout)
            else:
                u = db.get_user(uid)
                # جودة مختارة يدوياً: تحترمها دائماً.
                # الافتراضي (بدون اختيار محدد): 360p للعادي (سرعة قصوى) و720 للـ VIP.
                if max_height:
                    max_h = max_height
                else:
                    max_h = 720 if (u and u["is_vip"]) else 360
                path, title = await _retry_download(download_video, url, max_h, timeout=timeout)

            icon = "🎵" if kind == "audio" else "🎬"
            caption = f"{icon} <b>{esc(title or 'وسائط')}</b>\n<i>via {platform}</i>"
            size = os.path.getsize(path)
            try:
                if kind == "video":
                    # تأكد أن الصيغة يدعمها تيليجرام إن أمكن (أسرع مسار)،
                    # لكن لا نرفض الإرسال إطلاقاً — السرعة أولاً.
                    try:
                        converted = await asyncio.to_thread(ensure_telegram_compatible, path)
                    except DownloadError as conv_exc:
                        logger.warning("تحويل الفيديو فشل: %s", conv_exc)
                        converted = path
                    if converted != path:
                        logger.info("✅ تحويل الفيديو إلى صيغة MP4/H.264 يدعمها تيليجرام: %s", converted)
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                        path = converted
                    probe = await asyncio.to_thread(probe_media, path)
                    if not probe:
                        probe = {}
                    if probe.get("vcodec") != "h264":
                        logger.warning("⚠️ الفيديو ليس H.264 — نرسله مباشرة على أي حال (سرعة أولاً).")
                    # الطريقة المضمونة: إرسال عبر قناة رفع إن كانت مفعّلة
                    channel_id = db.get_setting("upload_channel_id", "").strip()
                    if channel_id:
                        up_kwargs = {}
                        if probe:
                            up_kwargs = {
                                "duration": probe["duration"],
                                "width": probe["width"],
                                "height": probe["height"],
                            }
                        elapsed_so_far = time.time() - start_time
                        chan_caption = (
                            f"{icon} <b>{esc(title or 'وسائط')}</b>\n"
                            f"<i>via {platform}</i>\n"
                            f"📺 من: @{esc(user_uname)} (<code>{uid}</code>)\n"
                            f"🗂 الحجم: {_fmt_size(size)}\n"
                            f"⏰ المعالجة: {elapsed_so_far:.1f} ثا\n"
                            f"🌐 المصدر: <code>{esc(url)}</code>"
                        )
                        chan_mid, resp = await uploader.a_upload_video_to_channel(
                            channel_id, path, chan_caption, **up_kwargs
                        )
                        if chan_mid:
                            # ارفع الفيديو للمستخدم بـ copyMessage (بدون metadata للمستخدم)
                            # clean_caption = بدون اسم مستخدم أو أيدي أو حجم
                            cpy = await uploader.a_copy_message(channel_id, chat_id, chan_mid, caption=caption)
                            sent_ok = (cpy.status_code == 200)
                        else:
                            sent_ok = False
                        if not sent_ok:
                            # حاول إرسال مباشر كخيار أخير
                            r = await uploader.a_send_video(chat_id, path, caption, **up_kwargs)
                            if r.status_code != 200:
                                logger.error("فشل إرسال الفيديو: %s", r.text[:200])
                                raise DownloadError(
                                    "❌ تعذر إرسال الفيديو عبر قناة الرفع. تأكد من ضبطها في لوحة المطور."
                                )
                    else:
                        up_kwargs = {}
                        if probe:
                            up_kwargs = {
                                "duration": probe["duration"],
                                "width": probe["width"],
                                "height": probe["height"],
                            }
                        r = await uploader.a_send_video(chat_id, path, caption, **up_kwargs)
                        if r.status_code != 200:
                            logger.error("فشل إرسال الفيديو: %s", r.text[:200])
                else:
                    r = await uploader.a_send_audio(chat_id, path, caption, title=title or "أغنية")
                    if r.status_code != 200:
                        logger.error("فشل إرسال الصوت: %s", r.text[:200])
            finally:
                cleanup(path)

            old = db.get_user(uid) or {}
            was_vip = bool(old.get("is_vip"))
            db.add_download_stats(uid, kind, title, size)
            db.add_points(uid, points)
            now = db.get_user(uid) or {}
            logger.info("✅ اكتمل التحميل: %s", title)
            if not was_vip and now.get("is_vip"):
                await uploader.a_send_message(
                    chat_id,
                    f"🎉 <b>مبروك! وصلت إلى {VIP_THRESHOLD} نقطة وأصبحت عضواً VIP!</b>\n"
                    "✅ إلغاء الاشتراك الإجباري\n"
                    "✅ جودة 1080p\n"
                    "✅ أولوية أعلى في التحميل",
                )
            fact_label, fact_text = next_fact(uid)
            # تأخير بسيط: ننتظر حتى يصل الفيديو للمستخدم أولاً
            await asyncio.sleep(2)
            # تقرير النقاط: رسالة مستقلة
            await uploader.a_edit_message_text(chat_id, status_id,
                f"✅ تم التحميل!\n⭐ +{points} نقطة (نقاطك الآن: {now.get('points', 0)})")
            # المعلومة: رسالة منفصلة تماماً
            try:
                await asyncio.sleep(1)
                await uploader.a_send_message(
                    chat_id,
                    f"💡 <b>معلومة {esc(fact_label)}:</b>\n{esc(fact_text)}"
                )
            except Exception:
                pass
        except asyncio.TimeoutError:
            logger.warning("⏱️ انتهت مهلة التحميل")
            try:
                await uploader.a_edit_message_text(chat_id, status_id,
                    "⏳ انتهت مهلة التحميل (الشبكة بطيئة أو المنصة حجبت الطلب).\n"
                    "جرّب مرة أخرى بعد قليل، أو أرسل رابطاً آخر.")
            except Exception:
                await uploader.a_send_message(chat_id,
                    "⏳ انتهت مهلة التحميل (الشبكة بطيئة أو المنصة حجبت الطلب).\n"
                    "جرّب مرة أخرى بعد قليل، أو أرسل رابطاً آخر.")
        except DownloadError as exc:
            logger.warning("DownloadError: %s", exc)
            try:
                await uploader.a_edit_message_text(chat_id, status_id, f"❌ {exc}")
            except Exception:
                await uploader.a_send_message(chat_id, f"❌ {exc}")
        except Exception:
            logger.error("Download failed:\n%s", traceback.format_exc())
            try:
                await uploader.a_edit_message_text(chat_id, status_id, "❌ حدث خطأ غير متوقع أثناء التحميل. حاول لاحقاً.")
            except Exception:
                try:
                    await uploader.a_send_message(chat_id, "❌ حدث خطأ غير متوقع أثناء التحميل. حاول لاحقاً.")
                except Exception:
                    pass
        finally:
            _USER_BUSY[uid] = False
            state.clear_queue_position(uid)
            logger.info("🏁 انتهت مهمة التحميل")

    enqueue(uid, do_job)


def _fmt_size(num):
    """يحوّل حجم الملف إلى مقروء (MB/GB)."""
    try:
        mb = num / (1024 * 1024)
        if mb >= 1024:
            return f"{mb/1024:.2f} GB"
        return f"{mb:.1f} MB"
    except Exception:
        return str(num)
