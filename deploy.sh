#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
#  deploy.sh — سكربت نشر البوت على أي سيرفر Linux
#  يشتغل على: Oracle Cloud VPS, Ubuntu, Debian, أي سيرفر
#  ◦ يثبّت كل المتطلبات
#  ◦ ينشئ خدمة systemd تبدأ تلقائياً وتعيد التشغيل عند الكراش
#  ◦ يحوّل الكود القديم (SQLite) ليعمل 24/7 بدون مشاكل
# ═══════════════════════════════════════════════════════════════

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

BOT_DIR="${BOT_DIR:-$HOME/tecno-bot}"
REPO_URL="https://github.com/nsrn9501-code/tecno9501.git"
SERVICE_NAME="tecno-bot"

echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo -e "${CYAN}   🤖 سكربت نشر بوت تيلجرام — نسخة 24/7     ${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo ""

# ────────────────────────────────────────────────
# 1) تحديث النظام وتثبيت المتطلبات الأساسية
# ────────────────────────────────────────────────
echo -e "${YELLOW}[1/7]${NC} تحديث النظام وتثبيت المتطلبات..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv git curl ffmpeg > /dev/null 2>&1
echo -e "${GREEN}  ✓${NC} النظام جاهز"

# ────────────────────────────────────────────────
# 2) تحميل/تحديث الكود من GitHub
# ────────────────────────────────────────────────
echo -e "${YELLOW}[2/7]${NC} تحميل البوت من GitHub..."
if [ -d "$BOT_DIR/.git" ]; then
    cd "$BOT_DIR"
    git pull origin main --quiet
    echo -e "${GREEN}  ✓${NC} الكود محدّث"
else
    git clone "$REPO_URL" "$BOT_DIR" --quiet
    cd "$BOT_DIR"
    echo -e "${GREEN}  ✓${NC} الكود محمل"
fi

# ────────────────────────────────────────────────
# 3) إنشاء بيئة Python الافتراضية وتثبيت الحزم
# ────────────────────────────────────────────────
echo -e "${YELLOW}[3/7]${NC} تجهيز بيئة Python..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip -q 2>/dev/null
./venv/bin/pip install -r requirements.txt -q 2>/dev/null
# yt-dlp المصدري: نثبّته من pip مباشرة (أحدث إصدار مع إصلاحات)
./venv/bin/pip install --upgrade yt-dlp -q 2>/dev/null
echo -e "${GREEN}  ✓${NC} كل الحزم مثبّتة"

# ────────────────────────────────────────────────
# 4) إعداد ملف .env
# ────────────────────────────────────────────────
echo -e "${YELLOW}[4/7]${NC} إعداد ملف .env..."

if [ ! -f ".env" ]; then
    echo -e "${CYAN}  أدخل التوكن والرقم:${NC}"
    read -rp "  BOT_TOKEN: " BT
    read -rp "  OWNER_ID: " OID
    cat > .env <<EOF
BOT_TOKEN=$BT
OWNER_ID=$OID
EOF
    chmod 600 .env
    echo -e "${GREEN}  ✓${NC} ملف .env تم إنشاؤه"
else
    echo -e "${GREEN}  ✓${NC} ملف .env موجود بالفعل (لن يتأثر)"
fi

# ────────────────────────────────────────────────
# 5) إنشاء المجلدات المطلوبة
# ────────────────────────────────────────────────
echo -e "${YELLOW}[5/7]${NC} إنشاء المجلدات..."
mkdir -p data bot/downloads bot/cookies
echo -e "${GREEN}  ✓${NC} المجلدات جاهزة"

# ────────────────────────────────────────────────
# 6) إنشاء خدمة systemd (تشغيل تلقائي + إعادة عند الكراش)
# ────────────────────────────────────────────────
echo -e "${YELLOW}[6/7]${NC} إنشاء خدمة التشغيل..."

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description= TecnoBot Telegram Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python run.py
Restart=always
RestartSec=5
StandardOutput=append:$BOT_DIR/bot.log
StandardError=append:$BOT_DIR/bot.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME} > /dev/null 2>&1
echo -e "${GREEN}  ✓${NC} الخدمة جاهزة"

# ────────────────────────────────────────────────
# 7) تشغيل البوت
# ────────────────────────────────────────────────
echo -e "${YELLOW}[7/7]${NC} تشغيل البوت..."
sudo systemctl restart ${SERVICE_NAME}
sleep 3

if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✅ البوت شغال بنجاح! 24/7 + إعادة تلقائية  ${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  📋 أوامر مفيدة:"
    echo -e "  ${CYAN}sudo systemctl status ${SERVICE_NAME}${NC}  — حالة البوت"
    echo -e "  ${CYAN}sudo systemctl restart ${SERVICE_NAME}${NC} — إعادة تشغيل"
    echo -e "  ${CYAN}sudo systemctl stop ${SERVICE_NAME}${NC}  — إيقاف مؤقت"
    echo -e "  ${CYAN}tail -f $BOT_DIR/bot.log${NC}            — متابعة السجل"
    echo ""
    echo -e "  📁 مسار البوت: ${CYAN}$BOT_DIR${NC}"
    echo -e "  📁 ملف السجل: ${CYAN}$BOT_DIR/bot.log${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}═══════════════════════════════════════════════${NC}"
    echo -e "${RED}  ❌ فشل التشغيل — راجع السجل:${NC}"
    echo -e "${RED}  sudo systemctl status ${SERVICE_NAME}${NC}"
    echo -e "${RED}  tail -30 $BOT_DIR/bot.log${NC}"
    echo -e "${RED}═══════════════════════════════════════════════${NC}"
fi
