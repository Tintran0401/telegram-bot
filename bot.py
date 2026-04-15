import feedparser
import requests
import logging
import pytz
import google.generativeai as genai
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os

BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

genai.configure(api_key=GEMINI_API_KEY)
gemini = genai.GenerativeModel("gemini-2.0-flash")

VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")
logging.basicConfig(level=logging.INFO)

last_bulletin = {"text": "", "time": ""}

RSS_SOURCES = {
    "🇻🇳 VnExpress Kinh tế": "https://vnexpress.net/rss/kinh-doanh.rss",
    "🇻🇳 CafeF":             "https://cafef.vn/rss/thi-truong-chung-khoan.rss",
    "🇻🇳 Tuổi Trẻ":          "https://tuoitre.vn/rss/kinh-te.rss",
    "🌍 Reuters":             "https://feeds.reuters.com/reuters/businessNews",
    "🌍 BBC Business":        "https://feeds.bbci.co.uk/news/business/rss.xml",
}

MAIN_MENU = [
    [
        InlineKeyboardButton("📊 Thông tin thị trường", callback_data="info"),
        InlineKeyboardButton("💰 Đầu tư", callback_data="dautu"),
    ],
    [
        InlineKeyboardButton("🤖 Hỏi AI phân tích", callback_data="ai_menu"),
        InlineKeyboardButton("🗑 Reset chat", callback_data="reset"),
    ]
]

AI_MENU = [
    [InlineKeyboardButton("📰 Tóm tắt bản tin",       callback_data="ai_summary")],
    [InlineKeyboardButton("📊 Phân tích thị trường",   callback_data="ai_analyze")],
    [InlineKeyboardButton("💡 Tư vấn đầu tư hôm nay", callback_data="ai_invest") ],
    [InlineKeyboardButton("💬 Chat tự do với AI",      callback_data="ai_chat")  ],
    [InlineKeyboardButton("🔙 Quay lại menu chính",    callback_data="back_main") ],
]

# ── Helpers ──────────────────────────────────────────────────
def get_usd_vnd():
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8)
        if r.status_code == 200:
            return r.json()["rates"].get("VND", 25400)
    except: pass
    return 25400

def format_vnd(usd_amount, rate):
    vnd = usd_amount * rate
    if vnd >= 1_000_000_000:
        return f"{vnd/1_000_000_000:.2f} tỷ ₫"
    elif vnd >= 1_000_000:
        return f"{vnd/1_000_000:.1f} tr ₫"
    return f"{vnd:,.0f} ₫"

def get_yahoo(ticker):
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d",
            timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            result = r.json().get("chart", {}).get("result", [])
            if result:
                meta = result[0]["meta"]
                p    = float(meta.get("regularMarketPrice", 0))
                prev = float(meta.get("chartPreviousClose", p))
                chg  = ((p - prev) / prev * 100) if prev else 0
                return p, chg
    except: pass
    return None, None

def get_market_data():
    usd_vnd = get_usd_vnd()
    lines = []
    lines.append(f"💵 USD/VND: {usd_vnd:,.0f} ₫")
    try:
        r = requests.get("https://api.gold-api.com/price/XAU", timeout=8)
        if r.status_code == 200:
            gold = r.json().get("price", 0)
            lines.append(f"🥇 Vàng (XAU): ${gold:,.2f}\n     ≈ {format_vnd(gold, usd_vnd)} / oz")
    except: pass
    try:
        r = requests.get("https://api.gold-api.com/price/XAG", timeout=8)
        if r.status_code == 200:
            silver = r.json().get("price", 0)
            lines.append(f"🥈 Bạc  (XAG): ${silver:,.2f}\n     ≈ {format_vnd(silver, usd_vnd)} / oz")
    except: pass
    lines.append("\n🇺🇸 *SÀN MỸ*")
    for name, ticker in [("S&P 500","%5EGSPC"),("NASDAQ","%5EIXIC"),("Dow Jones","%5EDJI")]:
        p, chg = get_yahoo(ticker)
        if p:
            arrow = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{arrow} {name}: {p:,.2f} ({chg:+.2f}%)\n     ≈ {format_vnd(p, usd_vnd)}")
        else:
            lines.append(f"⚠️ {name}: không lấy được")
    lines.append("\n🇻🇳 *SÀN VIỆT NAM*")
    vn_tickers = [
        ("VN-Index",   ["%5EVNINDEX.VN", "%5EVNINDEX"                   ]),
        ("VN30 (ETF)", ["E1VFVN30.VN",   "%5EVN30",     "%5EVNIPR"      ]),
        ("HNX",        ["%5EHNXINDEX",   "%5EHNX",      "%5EHNXINDEX.VN"]),
    ]
    for name, tickers in vn_tickers:
        found = False
        for ticker in tickers:
            p, chg = get_yahoo(ticker)
            if p and p > 0:
                arrow = "🟢" if chg >= 0 else "🔴"
                lines.append(f"{arrow} {name}: {p:,.2f} ({chg:+.2f}%)")
                found = True
                break
        if not found:
            lines.append(f"⏸ {name}: ngoài giờ / không có dữ liệu")
    return "\n".join(lines)

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
    last_bulletin["text"] = msg
    last_bulletin["time"] = now
    return msg

# ── Gemini AI ────────────────────────────────────────────────
def ask_gemini(prompt):
    try:
        response = gemini.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"⚠️ AI lỗi: {e}"

# ── Gửi bản tin ─────────────────────────────────────────────
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

async def send_menu(bot: Bot, chat_id):
    await bot.send_message(
        chat_id=chat_id,
        text="📌 Chọn mục tiếp theo:",
        reply_markup=InlineKeyboardMarkup(MAIN_MENU)
    )

# ── Commands ─────────────────────────────────────────────────
async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Chọn mục bạn muốn:",
        reply_markup=InlineKeyboardMarkup(MAIN_MENU)
    )

async def cmd_now(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ Lệnh /now đã bị vô hiệu hóa.\nVui lòng dùng menu:",
        reply_markup=InlineKeyboardMarkup(MAIN_MENU)
    )

# ── Chat tự do với AI ────────────────────────────────────────
async def handle_message(update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("ai_chat_mode"):
        user_text = update.message.text
        await update.message.reply_text("🤖 AI đang suy nghĩ...")
        bulletin  = last_bulletin.get("text", "Chưa có dữ liệu thị trường.")
        prompt = (
            f"Bạn là chuyên gia tài chính Việt Nam. Trả lời bằng tiếng Việt, "
            f"ngắn gọn, rõ ràng. Không đưa ra lời khuyên tài chính tuyệt đối.\n\n"
            f"Dữ liệu thị trường mới nhất:\n{bulletin}\n\n"
            f"Câu hỏi: {user_text}"
        )
        reply = ask_gemini(prompt)
        await update.message.reply_text(
            f"🤖 *AI trả lời:*\n\n{reply}",
            parse_mode="Markdown"
        )
        await update.message.reply_text(
            "💬 Tiếp tục hỏi hoặc thoát:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Thoát chat AI", callback_data="back_main")]
            ])
        )

# ── Xử lý nút bấm ───────────────────────────────────────────
async def button_handler(update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "info":
        await query.message.reply_text("⏳ Đang lấy dữ liệu, vui lòng chờ...")
        await send_update(context.bot)
        await send_menu(context.bot, chat_id)

    elif query.data == "dautu":
        await query.message.reply_text(
            "💰 *ĐẦU TƯ*\n━━━━━━━━━━━━━━━\n\n"
            "📌 Bản tin tự động gửi lúc:\n"
            "🕖 07:00 — 🕛 12:00 — 🕕 18:00\n\n"
            "Nhấn *Thông tin thị trường* để cập nhật ngay:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU)
        )

    elif query.data == "ai_menu":
        await query.message.reply_text(
            "🤖 *AI PHÂN TÍCH — Gemini*\n━━━━━━━━━━━━━━━\n"
            "Chọn tính năng AI bạn muốn:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(AI_MENU)
        )

    elif query.data == "ai_summary":
        if not last_bulletin["text"]:
            await query.message.reply_text(
                "⚠️ Chưa có bản tin!\nNhấn 📊 Thông tin thị trường trước.",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU)
            )
            return
        await query.message.reply_text("🤖 Gemini đang tóm tắt...")
        prompt = (
            f"Tóm tắt bản tin kinh tế sau thành 5-7 điểm quan trọng nhất, "
            f"dễ hiểu, bằng tiếng Việt, dùng emoji:\n\n{last_bulletin['text']}"
        )
        reply = ask_gemini(prompt)
        await query.message.reply_text(
            f"🤖 *TÓM TẮT — {last_bulletin['time']}*\n\n{reply}",
            parse_mode="Markdown"
        )
        await send_menu(context.bot, chat_id)

    elif query.data == "ai_analyze":
        if not last_bulletin["text"]:
            await query.message.reply_text(
                "⚠️ Chưa có bản tin!\nNhấn 📊 Thông tin thị trường trước.",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU)
            )
            return
        await query.message.reply_text("🤖 Gemini đang phân tích thị trường...")
        prompt = (
            f"Phân tích xu hướng thị trường tài chính từ dữ liệu sau. "
            f"Chỉ ra điểm đáng chú ý, rủi ro và cơ hội. "
            f"Trả lời bằng tiếng Việt, súc tích, dùng emoji:\n\n{last_bulletin['text']}"
        )
        reply = ask_gemini(prompt)
        await query.message.reply_text(
            f"🤖 *PHÂN TÍCH THỊ TRƯỜNG*\n\n{reply}",
            parse_mode="Markdown"
        )
        await send_menu(context.bot, chat_id)

    elif query.data == "ai_invest":
        if not last_bulletin["text"]:
            await query.message.reply_text(
                "⚠️ Chưa có bản tin!\nNhấn 📊 Thông tin thị trường trước.",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU)
            )
            return
        await query.message.reply_text("🤖 Gemini đang đưa ra góc nhìn đầu tư...")
        prompt = (
            f"Dựa trên dữ liệu thị trường, đưa ra góc nhìn đầu tư ngắn hạn hôm nay "
            f"cho nhà đầu tư cá nhân Việt Nam: nên chú ý kênh nào (vàng, chứng khoán, "
            f"USD, crypto...), lý do ngắn gọn. Viết bằng tiếng Việt, dùng emoji. "
            f"Luôn nhắc đây chỉ là tham khảo.\n\n{last_bulletin['text']}"
        )
        reply = ask_gemini(prompt)
        await query.message.reply_text(
            f"🤖 *GÓC NHÌN ĐẦU TƯ HÔM NAY*\n\n{reply}\n\n"
            f"⚠️ _Chỉ mang tính tham khảo._",
            parse_mode="Markdown"
        )
        await send_menu(context.bot, chat_id)

    elif query.data == "ai_chat":
        context.user_data["ai_chat_mode"] = True
        await query.message.reply_text(
            "💬 *CHAT TỰ DO VỚI GEMINI AI*\n━━━━━━━━━━━━━━━\n"
            "Gõ câu hỏi bất kỳ về kinh tế, đầu tư...\n\n"
            "Ví dụ:\n"
            "• _Vàng có nên mua lúc này không?_\n"
            "• _Tại sao USD/VND tăng?_\n"
            "• _Fed là gì, ảnh hưởng thế nào?_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Thoát chat AI", callback_data="back_main")]
            ])
        )

    elif query.data == "back_main":
        context.user_data["ai_chat_mode"] = False
        await query.message.reply_text(
            "👋 Quay lại menu chính:",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU)
        )

    elif query.data == "reset":
        keyboard = [[
            InlineKeyboardButton("✅ Xác nhận xóa", callback_data="reset_confirm"),
            InlineKeyboardButton("❌ Hủy",          callback_data="reset_cancel"),
        ]]
        await query.message.reply_text(
            "⚠️ *Bạn có chắc muốn xóa toàn bộ lịch sử chat không?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "reset_confirm":
        msg_id  = query.message.message_id
        deleted = 0
        for i in range(msg_id, max(msg_id - 200, 0), -1):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=i)
                deleted += 1
            except: pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Đã xóa {deleted} tin nhắn.\n\n👋 Chọn mục bạn muốn:",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU)
        )

    elif query.data == "reset_cancel":
        await query.message.reply_text(
            "↩️ Đã hủy.",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU)
        )

# ── Gửi tự động kèm menu ────────────────────────────────────
async def scheduled_update(bot: Bot):
    await send_update(bot)
    await send_menu(bot, CHAT_ID)

# ── Main ─────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("now",   cmd_now))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler(timezone=VN_TZ)
    for hour in [7, 12, 18]:
        scheduler.add_job(scheduled_update, "cron", hour=hour, minute=0, args=[app.bot])
    scheduler.start()

    logging.info("🚀 Bot chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
