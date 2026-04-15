import feedparser
import requests
import logging
import pytz
import asyncio
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os

BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

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

# ── Fetch song song ──────────────────────────────────────────
def fetch(url, timeout=6):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return r.json()
    except: pass
    return {}

def get_yahoo(ticker):
    try:
        data = fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d")
        result = data.get("chart", {}).get("result", [])
        if result:
            meta = result[0]["meta"]
            p    = float(meta.get("regularMarketPrice", 0))
            prev = float(meta.get("chartPreviousClose", p))
            chg  = ((p - prev) / prev * 100) if prev else 0
            return p, chg
    except: pass
    return None, None

def format_vnd(usd_amount, rate):
    vnd = usd_amount * rate
    if vnd >= 1_000_000_000:
        return f"{vnd/1_000_000_000:.2f} tỷ ₫"
    elif vnd >= 1_000_000:
        return f"{vnd/1_000_000:.1f} tr ₫"
    return f"{vnd:,.0f} ₫"

# ── Lấy tất cả dữ liệu song song ────────────────────────────
async def get_market_data_async():
    loop = asyncio.get_event_loop()

    # Chạy tất cả API cùng lúc trong thread pool
    tasks = await asyncio.gather(
        loop.run_in_executor(None, fetch, "https://open.er-api.com/v6/latest/USD"),
        loop.run_in_executor(None, fetch, "https://api.gold-api.com/price/XAU"),
        loop.run_in_executor(None, fetch, "https://api.gold-api.com/price/XAG"),
        loop.run_in_executor(None, get_yahoo, "%5EGSPC"),
        loop.run_in_executor(None, get_yahoo, "%5EIXIC"),
        loop.run_in_executor(None, get_yahoo, "%5EDJI"),
        loop.run_in_executor(None, get_yahoo, "%5EVNINDEX.VN"),
        loop.run_in_executor(None, get_yahoo, "E1VFVN30.VN"),
        loop.run_in_executor(None, get_yahoo, "%5EHNXINDEX"),
    )

    fx_data, gold_data, silver_data, sp, nasdaq, dji, vnindex, vn30, hnx = tasks

    usd_vnd = fx_data.get("rates", {}).get("VND", 25400) if fx_data else 25400
    lines   = []

    # Tỷ giá
    lines.append(f"💵 USD/VND: {usd_vnd:,.0f} ₫")

    # Vàng & Bạc
    if gold_data:
        g = gold_data.get("price", 0)
        lines.append(f"🥇 Vàng (XAU): ${g:,.2f}\n     ≈ {format_vnd(g, usd_vnd)} / oz")
    if silver_data:
        s = silver_data.get("price", 0)
        lines.append(f"🥈 Bạc  (XAG): ${s:,.2f}\n     ≈ {format_vnd(s, usd_vnd)} / oz")

    # Sàn Mỹ
    lines.append("\n🇺🇸 *SÀN MỸ*")
    for name, result in [("S&P 500", sp), ("NASDAQ", nasdaq), ("Dow Jones", dji)]:
        if result and result[0]:
            p, chg = result
            arrow  = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{arrow} {name}: {p:,.2f} ({chg:+.2f}%)\n     ≈ {format_vnd(p, usd_vnd)}")
        else:
            lines.append(f"⚠️ {name}: không lấy được")

    # Sàn Việt Nam
    lines.append("\n🇻🇳 *SÀN VIỆT NAM*")
    vn_results = [
        ("VN-Index",   vnindex),
        ("VN30 (ETF)", vn30),
        ("HNX",        hnx),
    ]
    for name, result in vn_results:
        if result and result[0] and result[0] > 0:
            p, chg = result
            arrow  = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{arrow} {name}: {p:,.2f} ({chg:+.2f}%)")
        else:
            lines.append(f"⏸ {name}: ngoài giờ / không có dữ liệu")

    return "\n".join(lines)

# ── Tin tức song song ────────────────────────────────────────
async def get_news_async():
    loop   = asyncio.get_event_loop()
    blocks = []

    async def parse_feed(name, url):
        try:
            feed = await loop.run_in_executor(None, feedparser.parse, url)
            if not feed.entries:
                return ""
            block = f"\n{name}"
            for e in feed.entries[:2]:
                block += f"\n📌 {e.get('title','').strip()[:120]}\n🔗 {e.get('link','')}"
            return block
        except:
            return ""

    results = await asyncio.gather(*[
        parse_feed(name, url) for name, url in RSS_SOURCES.items()
    ])
    return [r for r in results if r]

# ── Bản tin hoàn chỉnh ───────────────────────────────────────
async def build_message_async():
    now = datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")

    # Lấy thị trường và tin tức song song cùng lúc
    market, news_blocks = await asyncio.gather(
        get_market_data_async(),
        get_news_async()
    )

    msg = (f"📊 *CẬP NHẬT KINH TẾ — {now}*\n"
           f"━━━━━━━━━━━━━━━\n\n"
           f"📈 *THỊ TRƯỜNG*\n{market}\n\n"
           f"━━━━━━━━━━━━━━━\n📰 *TIN TỨC*")
    for b in news_blocks:
        msg += f"\n{b}\n"
    msg += "\n━━━━━━━━━━━━━━━"
    last_bulletin["text"] = msg
    last_bulletin["time"] = now
    return msg

# ── Gemini AI (async) ────────────────────────────────────────
async def ask_gemini_async(prompt):
    try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.7}
        }
        loop = asyncio.get_event_loop()
        def _call():
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return f"⚠️ AI lỗi: {r.status_code} — {r.text[:200]}"
        return await loop.run_in_executor(None, _call)
    except Exception as e:
        return f"⚠️ AI lỗi: {e}"

# ── Gửi bản tin ─────────────────────────────────────────────
async def send_update(bot: Bot):
    try:
        msg = await build_message_async()
        await bot.send_message(
            chat_id=CHAT_ID,
            text=msg,
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
        bulletin  = last_bulletin.get("text", "Chưa có dữ liệu.")
        prompt = (
            f"Bạn là chuyên gia tài chính Việt Nam. Trả lời ngắn gọn bằng tiếng Việt. "
            f"Không đưa lời khuyên tài chính tuyệt đối.\n\n"
            f"Dữ liệu thị trường:\n{bulletin}\n\nCâu hỏi: {user_text}"
        )
        reply = await ask_gemini_async(prompt)
        await update.message.reply_text(f"🤖 *AI:*\n\n{reply}", parse_mode="Markdown")
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
        await query.message.reply_text("⏳ Đang lấy dữ liệu...")
        await send_update(context.bot)
        await send_menu(context.bot, chat_id)

    elif query.data == "dautu":
        await query.message.reply_text(
            "💰 *ĐẦU TƯ*\n━━━━━━━━━━━━━━━\n\n"
            "📌 Bản tin tự động lúc:\n"
            "🕖 07:00 — 🕛 12:00 — 🕕 18:00",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU)
        )

    elif query.data == "ai_menu":
        await query.message.reply_text(
            "🤖 *AI PHÂN TÍCH — Gemini*\n━━━━━━━━━━━━━━━",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(AI_MENU)
        )

    elif query.data in ("ai_summary", "ai_analyze", "ai_invest"):
        if not last_bulletin["text"]:
            await query.message.reply_text(
                "⚠️ Nhấn 📊 Thông tin thị trường trước!",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU)
            )
            return

        prompts = {
            "ai_summary": (
                "🤖 Gemini đang tóm tắt...",
                "📰 *TÓM TẮT BẢN TIN*",
                f"Tóm tắt bản tin sau thành 5-7 điểm quan trọng, dễ hiểu, "
                f"tiếng Việt, dùng emoji:\n\n{last_bulletin['text']}"
            ),
            "ai_analyze": (
                "🤖 Gemini đang phân tích...",
                "📊 *PHÂN TÍCH THỊ TRƯỜNG*",
                f"Phân tích xu hướng thị trường, chỉ ra rủi ro và cơ hội, "
                f"tiếng Việt, súc tích, emoji:\n\n{last_bulletin['text']}"
            ),
            "ai_invest": (
                "🤖 Gemini đang phân tích đầu tư...",
                "💡 *GÓC NHÌN ĐẦU TƯ*",
                f"Góc nhìn đầu tư ngắn hạn cho nhà đầu tư cá nhân Việt Nam "
                f"dựa trên dữ liệu. Nhắc đây chỉ là tham khảo. "
                f"Tiếng Việt, emoji:\n\n{last_bulletin['text']}"
            ),
        }
        waiting, title, prompt = prompts[query.data]
        await query.message.reply_text(waiting)
        reply = await ask_gemini_async(prompt)
        await query.message.reply_text(
            f"{title} — {last_bulletin['time']}\n\n{reply}",
            parse_mode="Markdown"
        )
        await send_menu(context.bot, chat_id)

    elif query.data == "ai_chat":
        context.user_data["ai_chat_mode"] = True
        await query.message.reply_text(
            "💬 *CHAT VỚI GEMINI AI*\n━━━━━━━━━━━━━━━\n"
            "Gõ câu hỏi về kinh tế, đầu tư...\n\n"
            "• _Vàng có nên mua không?_\n"
            "• _Tại sao USD/VND tăng?_\n"
            "• _Fed là gì?_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Thoát", callback_data="back_main")]
            ])
        )

    elif query.data == "back_main":
        context.user_data["ai_chat_mode"] = False
        await query.message.reply_text(
            "👋 Menu chính:",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU)
        )

    elif query.data == "reset":
        await query.message.reply_text(
            "⚠️ *Xác nhận xóa lịch sử chat?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Xóa", callback_data="reset_confirm"),
                InlineKeyboardButton("❌ Hủy", callback_data="reset_cancel"),
            ]])
        )

    elif query.data == "reset_confirm":
        msg_id = query.message.message_id
        deleted = 0
        for i in range(msg_id, max(msg_id - 200, 0), -1):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=i)
                deleted += 1
            except: pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Đã xóa {deleted} tin nhắn.",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU)
        )

    elif query.data == "reset_cancel":
        await query.message.reply_text("↩️ Đã hủy.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))

# ── Tự động ─────────────────────────────────────────────────
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
