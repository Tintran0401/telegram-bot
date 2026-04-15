import feedparser
import requests
import logging
import pytz
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
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

def get_usd_vnd():
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8)
        if r.status_code == 200:
            return r.json()["rates"].get("VND", 25400)
    except: pass
    return 25400

def format_vnd(usd_amount, usd_vnd_rate):
    vnd = usd_amount * usd_vnd_rate
    if vnd >= 1_000_000_000:
        return f"{vnd/1_000_000_000:.2f} tỷ ₫"
    elif vnd >= 1_000_000:
        return f"{vnd/1_000_000:.1f} tr ₫"
    else:
        return f"{vnd:,.0f} ₫"

def get_market_data():
    usd_vnd = get_usd_vnd()
    lines = []

    # ── Tỷ giá ──────────────────────────────────────────────
    lines.append(f"💵 USD/VND: {usd_vnd:,.0f} ₫")

    # ── Vàng & Bạc ──────────────────────────────────────────
    try:
        r = requests.get("https://api.gold-api.com/price/XAU", timeout=8)
        if r.status_code == 200:
            gold = r.json().get("price", 0)
            lines.append(
                f"🥇 Vàng (XAU): ${gold:,.2f}\n"
                f"     ≈ {format_vnd(gold, usd_vnd)} / troy oz"
            )
    except: pass

    try:
        r = requests.get("https://api.gold-api.com/price/XAG", timeout=8)
        if r.status_code == 200:
            silver = r.json().get("price", 0)
            lines.append(
                f"🥈 Bạc  (XAG): ${silver:,.2f}\n"
                f"     ≈ {format_vnd(silver, usd_vnd)} / troy oz"
            )
    except: pass

    # ── Sàn Mỹ ──────────────────────────────────────────────
    lines.append("\n🇺🇸 *SÀN MỸ*")
    try:
        tickers = [
            ("S&P 500",  "%5EGSPC"),
            ("NASDAQ",   "%5EIXIC"),
            ("Dow Jones","%5EDJI"),
        ]
        for name, ticker in tickers:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d",
                timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                meta = r.json()["chart"]["result"][0]["meta"]
                p    = meta.get("regularMarketPrice", 0)
                prev = meta.get("chartPreviousClose", p)
                chg  = ((p - prev) / prev * 100) if prev else 0
                arrow = "🟢" if chg >= 0 else "🔴"
                lines.append(
                    f"{arrow} {name}: {p:,.2f} ({chg:+.2f}%)\n"
                    f"     ≈ {format_vnd(p, usd_vnd)}"
                )
    except: pass

   # ── Sàn Việt Nam (VNDirect API) ─────────────────────────
    lines.append("\n🇻🇳 *SÀN VIỆT NAM*")
    try:
        vn_indices = [
            ("VN-Index", "VNINDEX"),
            ("VN30",     "VN30"),
            ("HNX",      "HNX"),
        ]
        for name, code in vn_indices:
            try:
                r = requests.get(
                    f"https://finfo-api.vndirect.com.vn/v4/indices?q=code:{code}&size=1",
                    timeout=8,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "application/json"
                    }
                )
                if r.status_code == 200:
                    item = r.json().get("data", [{}])[0]
                    p    = float(item.get("indexValue", 0))
                    chg  = float(item.get("percentChange", 0))
                    vol  = int(item.get("totalVolume", 0))
                    arrow = "🟢" if chg >= 0 else "🔴"
                    lines.append(
                        f"{arrow} {name}: {p:,.2f} ({chg:+.2f}%)\n"
                        f"     KL: {vol:,} cp"
                    )
                else:
                    lines.append(f"⚠️ {name}: không lấy được")
            except:
                lines.append(f"⚠️ {name}: lỗi kết nối")
    except Exception as e:
        lines.append(f"⚠️ Lỗi sàn VN: {e}")

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
        await bot.send_message(
            chat_id=CHAT_ID,
            text=build_message(),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        logging.info("✅ Đã gửi")
    except Exception as e:
        logging.error(f"❌ {e}")

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("📊 Thông tin thị trường", callback_data="info"),
            InlineKeyboardButton("💰 Đầu tư", callback_data="dautu"),
        ],
        [
            InlineKeyboardButton("🗑 Reset — Xóa lịch sử chat", callback_data="reset"),
        ]
    ]
    await update.message.reply_text(
        "👋 Xin chào! Chọn mục bạn muốn:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cmd_now(update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 Thông tin thị trường", callback_data="info")]
    ]
    await update.message.reply_text(
        "⚠️ Lệnh /now đã bị vô hiệu hóa.\nVui lòng dùng menu bên dưới:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "info":
        await query.message.reply_text("⏳ Đang lấy dữ liệu...")
        await send_update(context.bot)
        await query.message.reply_text("💬 Bạn có đang đầu tư không?")

    elif query.data == "dautu":
        keyboard = [
            [InlineKeyboardButton("📊 Cập nhật thị trường ngay", callback_data="info")]
        ]
        await query.message.reply_text(
            "💰 *ĐẦU TƯ*\n━━━━━━━━━━━━━━━\n\n"
            "📌 Bản tin tự động gửi lúc:\n"
            "🕖 07:00 — 🕛 12:00 — 🕕 18:00\n\n"
            "Nhấn nút bên dưới để cập nhật ngay:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "reset":
        keyboard = [
            [
                InlineKeyboardButton("✅ Xác nhận xóa", callback_data="reset_confirm"),
                InlineKeyboardButton("❌ Hủy", callback_data="reset_cancel"),
            ]
        ]
        await query.message.reply_text(
            "⚠️ *Bạn có chắc muốn xóa toàn bộ lịch sử chat không?*\n"
            "Hành động này không thể hoàn tác!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "reset_confirm":
        chat_id = query.message.chat_id
        msg_id  = query.message.message_id
        deleted = 0
        for i in range(msg_id, max(msg_id - 200, 0), -1):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=i)
                deleted += 1
            except: pass
        keyboard = [
            [
                InlineKeyboardButton("📊 Thông tin thị trường", callback_data="info"),
                InlineKeyboardButton("💰 Đầu tư", callback_data="dautu"),
            ],
            [
                InlineKeyboardButton("🗑 Reset — Xóa lịch sử chat", callback_data="reset"),
            ]
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Đã xóa {deleted} tin nhắn.\n\n👋 Chọn mục bạn muốn:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "reset_cancel":
        await query.message.reply_text("↩️ Đã hủy. Lịch sử chat giữ nguyên.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("now",   cmd_now))
    app.add_handler(CallbackQueryHandler(button_handler))

    scheduler = AsyncIOScheduler(timezone=VN_TZ)
    for hour in [7, 12, 18]:
        scheduler.add_job(send_update, "cron", hour=hour, minute=0, args=[app.bot])
    scheduler.start()

    logging.info("🚀 Bot chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
