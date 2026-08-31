import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
INSTAGRAM_SESSION_ID = os.getenv("INSTAGRAM_SESSION_ID", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# مسار التخزين الدائم — على HuggingFace Spaces المقروء/الكتابة يتم في /data
# (مجلد مركز البيانات يُمسح عند كل إعادة تشغيل، بينما /data يبقى)
if os.getenv("HF_SPACE", ""):
    DATA_ROOT = "/data"
else:
    DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

DB_PATH = os.path.join(DATA_ROOT, "bot.db")
DOWNLOAD_DIR = os.path.join(DATA_ROOT, "downloads")
COOKIES_DIR = os.path.join(DATA_ROOT, "cookies")

os.makedirs(DATA_ROOT, exist_ok=True)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB Telegram upload limit
DAILY_LIMIT_FREE = 10
DAILY_LIMIT_VIP = 50
DAILY_LINK_LIMIT_FREE = 7
DAILY_LINK_LIMIT_VIP = 30
DAILY_SEARCH_LIMIT_FREE = 3
DAILY_SEARCH_LIMIT_VIP = 30
RATE_WINDOW_SECONDS = 60
RATE_MAX_LINKS = 3
RATE_BAN_SECONDS = 30 * 60
POINTS_PER_VIDEO = 5
POINTS_PER_AUDIO = 3
POINTS_PER_REFERRAL = 20
DAILY_REWARD_POINTS = 10
GIFT_POINTS = 10
VIP_THRESHOLD = 500

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIES_DIR, exist_ok=True)
