import feedparser
import requests
import logging
import pytz
from datetime import datetime
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")
logging.basicConfig(level=logging.INFO)

RSS_SOURCES = {
    "🇻🇳 VnExpress Kinh tế": "https://vnexpress.net/rss/kinh-doanh.rss",
    "🇻🇳 CafeF":             "https://cafef.vn/rss/thi-truong-chung-khoan.rss",
    "🇻🇳 Tuổi Trẻ":          "https://tuoitre.vn/rss/kinh-te.rss",
    "🌍 Reuters":             "https://feeds.reuters.com/reuters/businessNews",
    "🌍 BBC Business":        "https://feeds.bbci.co.uk/news/business/rss.xml",
}

def get_market_data():
    lines = []
    try:
        r = requests.get("https://api.gold-api.com/price/XAU", timeout=8)
        if r.status_code == 200:
            lines.append(f"🥇 Vàng: ${r.json().get('price',0):,.2f}")
    except: pass
    try:
        r = requests.get("https://api.gold-api.com/price/XAG", timeout=8)
        if r.status_code == 200:
            lines.append(f"🥈 Bạc:  ${r.json().get('price',0):,.2f}")
    except: pass
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8)
        if r.status_code == 200:
            lines.append(f"💵 USD/VND: {r.json()['rates'].get('VND',0):,.0f}")
    except: pass
    try:
        for name, ticker in {"S&P500":"%5EGSPC","NASDAQ":"%5EIXIC","DJI":"%5EDJI"}.items():
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d",
                timeout=8, headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                meta = r.json()["chart"]["result"][0]["meta"]
                p = meta.get("regularMarketPrice",0)
                prev = meta.get("chartPreviousClose", p)
                chg = ((p-prev)/prev*100) if prev else 0
                lines.append(f"{'🟢' if chg>=0 else '🔴'} {name}: {p:,.2f} ({chg:+.2f}%)")
    except: pass
    return "\n".join(lines) or "⚠️ Không lấy được dữ liệu"

def get_news():
    blocks = []
    for name, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries: continue
            block = f"\n{name}"
            for e in feed.entries[:2]:
                block += f"\n📌 {e.get('title','').strip()[:120]}\n🔗 {e.get('link','')}"
            blocks.append(block)
        except: continue
    return blocks

def build_message():
    now = datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    msg = (f"📊 *CẬP NHẬT KINH TẾ — {now}*\n"
           f"━━━━━━━━━━━━━━━\n\n"
           f"📈 *THỊ TRƯỜNG*\n{get_market_data()}\n\n"
           f"━━━━━━━━━━━━━━━\n📰 *TIN TỨC*")
    for b in get_news():
        msg += f"\n{b}\n"
    msg += "\n━━━━━━━━━━━━━━━"
    return msg

async def send_update(bot: Bot):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=build_message(),
                               parse_mode="Markdown", disable_web_page_preview=True)
        logging.info("✅ Đã gửi")
    except Exception as e:
        logging.error(f"❌ {e}")

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot đang chạy!\nGõ /now để nhận bản tin ngay.")

async def cmd_now(update, context: ContextTypes.DEFAULT_TYPE):
    await send_update(context.bot)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("now", cmd_now))
    scheduler = AsyncIOScheduler(timezone=VN_TZ)
    for hour in [7, 12, 18]:
        scheduler.add_job(send_update, "cron", hour=hour, minute=0, args=[app.bot])
    scheduler.start()
    logging.info("🚀 Bot chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
