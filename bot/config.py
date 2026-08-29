import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bot.db")
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot", "downloads")
COOKIES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot", "cookies")
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB Telegram upload limit
DAILY_LIMIT_FREE = 3
DAILY_LIMIT_VIP = 20
POINTS_PER_VIDEO = 5
POINTS_PER_AUDIO = 3
POINTS_PER_REFERRAL = 20
DAILY_REWARD_POINTS = 10
GIFT_POINTS = 10
VIP_THRESHOLD = 500

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIES_DIR, exist_ok=True)
