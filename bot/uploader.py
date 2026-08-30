"""إرسال الملفات (فيديو) عبر Bot API HTTP مباشرة بـ `requests`.
هذه الطريقة أثبتت نجاحها في تشغيل الفيديو داخل تيليجرام (اختبار 11)
بدلاً من مكتبة python-telegram-bot التي كانت تُرسل فيديو يطلب مشغل خارجي."""
import requests

from .config import BOT_TOKEN

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_video(chat_id, path, caption, duration=0, width=0, height=0):
    """يرسل فيديو عبر sendVideo مباشرة بـ HTTP multipart (نفس اختبار 11 الناجح)."""
    filename = str(path).split("/")[-1] or "video.mp4"
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML",
        "supports_streaming": True,
    }
    if duration:
        data["duration"] = int(duration)
    if width:
        data["width"] = int(width)
    if height:
        data["height"] = int(height)
    with open(path, "rb") as f:
        files = {"video": (filename, f, "video/mp4")}
        return requests.post(f"{API}/sendVideo", data=data, files=files, timeout=300)


def send_audio(chat_id, path, caption, title=None):
    """يرسل صوت عبر sendAudio مباشرة بـ HTTP multipart."""
    filename = str(path).split("/")[-1] or "audio.mp3"
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if title:
        data["title"] = title
    with open(path, "rb") as f:
        files = {"audio": (filename, f, "audio/mpeg" if str(path).endswith(".mp3") else "application/octet-stream")}
        return requests.post(f"{API}/sendAudio", data=data, files=files, timeout=300)


def send_message(chat_id, text):
    """يرسل رسالة نصية HTML."""
    return requests.post(
        f"{API}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=60,
    )


def edit_message_text(chat_id, message_id, text):
    """يعدّل نص رسالة الحالة."""
    return requests.post(
        f"{API}/editMessageText",
        data={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"},
        timeout=60,
    )
