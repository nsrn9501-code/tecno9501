# Tecno Bot 🤖

Telegram bot for downloading videos and music from YouTube, Instagram, TikTok, and Facebook.

## Features
- Download videos and music from multiple platforms
- VIP system with daily limits
- Multi-channel subscription support
- Admin panel with broadcast, gift links, and user management
- Daily rewards and referral system
- Rate limiting and anti-spam protection

## Deployment (PythonAnywhere)

### Quick Start
1. Create a free account at [PythonAnywhere](https://www.pythonanywhere.com)
2. Upload the bot files via the Dashboard > Files
3. Open a Bash console and run:
   ```bash
   cd ~/<your_username>
   pip install --user -r requirements.txt
   ```
4. Set environment variables:
   - Go to **Account** > **Environment variables**
   - Add `BOT_TOKEN` = your bot token
   - Add `OWNER_ID` = your Telegram user ID
5. Set up an **Always-on Task**:
   - Go to **Tasks** > **Always-on Tasks**
   - Add a new task: `python3 /home/<your_username>/run.py`
   - Click **Save** and **Start**

### Notes
- SQLite database is stored in `data/bot.db`
- The bot runs 24/7 on the free tier
- No credit card required

## License
Private — for personal use only.
