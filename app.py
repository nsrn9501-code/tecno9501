"""HF Spaces entry point — Gradio wrapper keeps the process alive
while the Telegram bot runs in a background thread."""
import sys
import os
import threading

# تثبيت FFmpeg إذا لم يكن متاحاً (على HF Spaces المجاني)
if not os.path.exists("/usr/bin/ffmpeg"):
    os.system("apt-get update -qq && apt-get install -y -qq ffmpeg > /dev/null 2>&1")

# تشغيل البوت في thread منفصل
def run_bot():
    from bot.main import build_app
    app = build_app()
    app.run_polling(allowed_updates=None)

bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# واجهة Gradio بسيطة (تحافظ على بقاء HF Spaces حياً)
try:
    import gradio as gr
    demo = gr.Interface(fn=lambda x: "Bot is running! 🤖", inputs="text", outputs="text", title="Tecno Bot")
    demo.launch(server_name="0.0.0.0", server_port=7860)
except Exception as e:
    print(f"Bot started: {e}")
    # إذا فشل Gradio، نخلي البوت يشتغل بدون واجهة
    bot_thread.join()
