import os
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    CommandHandler,
    filters
)

# ==========================================
# إعدادات البوت والبيئة
# ==========================================
# يفضل وضع التوكن في Environment Variables في Render
# لكن يمكنك تركه هنا إذا أردت
TOKEN = os.getenv("TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")

ASK_TITLE = 1
DOWNLOAD_CHUNK = 1024 * 1024   # 1MB
TELEGRAM_LIMIT = 48 * 1024 * 1024  # 48MB (حد آمن)

# ==========================================
# 1. السيرفر الوهمي (لحل مشكلة Render)
# ==========================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    print(f"🌍 Web server started on port {port}")
    server.serve_forever()

# ==========================================
# 2. وظائف التحميل والرفع
# ==========================================
def stream_download(url, out_path):
    # إضافة headers لتجنب حظر بعض المواقع
    headers = {'User-Agent': 'Mozilla/5.0'}
    with requests.get(url, stream=True, timeout=30, headers=headers) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK):
                if chunk:
                    f.write(chunk)
    return os.path.getsize(out_path)

def upload_to_gofile(file_path):
    # جلب أفضل سيرفر متاح حالياً
    api_url = "https://api.gofile.io/getServer"
    server_data = requests.get(api_url).json()
    
    if server_data["status"] != "ok":
        raise Exception("فشل الاتصال بخوادم GoFile")
        
    server = server_data["data"]["server"]
    upload_url = f"https://{server}.gofile.io/uploadFile"

    with open(file_path, "rb") as f:
        # ملاحظة: GoFile أحياناً يتطلب توكن للحسابات، لكن الضيف يعمل غالباً
        files = {"file": f}
        r = requests.post(upload_url, files=files)

    data = r.json()
    if data["status"] != "ok":
        raise Exception("GoFile upload failed")

    return data["data"]["downloadPage"]

# ==========================================
# 3. مراحل المحادثة (Handlers)
# ==========================================
async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    
    # تحقق بسيط أن الرابط يبدأ بـ http
    if not url.startswith("http"):
        await update.message.reply_text("❌ يرجى إرسال رابط صحيح.")
        return ConversationHandler.END

    context.user_data["url"] = url
    await update.message.reply_text("📄 أرسل اسم الحلقة (أو الملف) الآن:")
    return ASK_TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    url = context.user_data.get("url")
    user_id = update.effective_user.id

    # تنظيف الاسم ليكون آمناً كاسم ملف
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_()[]")
    if not safe_title:
        safe_title = "video"
        
    # إضافة ID المستخدم لمنع تداخل الملفات
    filename = f"{user_id}_{safe_title}.mp4"

    await update.message.reply_text("⏳ جاري التحميل إلى السيرفر...")

    try:
        # عملية التحميل (Blocking)
        # ملاحظة: في البوتات الكبيرة يفضل تشغيل هذا في thread منفصل، لكن هنا مقبول
        size = stream_download(url, filename)
    except Exception as e:
        await update.message.reply_text(f"❌ فشل التحميل من الرابط:\n{e}")
        if os.path.exists(filename):
            os.remove(filename)
        return ConversationHandler.END

    # السيناريو 1: الملف صغير (إرسال مباشر)
    if size < TELEGRAM_LIMIT:
        await update.message.reply_text("📤 الملف مناسب، جاري الرفع لتيليجرام...")
        try:
            with open(filename, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.message.chat_id,
                    document=f,
                    filename=f"{safe_title}.mp4",
                    caption=f"🎬 {title}",
                    read_timeout=60, 
                    write_timeout=60, 
                    connect_timeout=60
                )
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ أثناء الإرسال لتيليجرام:\n{e}")
    
    # السيناريو 2: الملف كبير (GoFile)
    else:
        await update.message.reply_text(f"⚠️ حجم الملف ({size//(1024*1024)}MB) أكبر من حد البوت.\n🚀 جاري الرفع إلى GoFile...")
        try:
            link = upload_to_gofile(filename)
            await update.message.reply_text(
                f"✅ تم الرفع بنجاح!\n🎬 **{title}**\n🔗 رابط التحميل:\n{link}",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ فشل الرفع إلى GoFile:\n{e}")

    # تنظيف الملف من السيرفر
    if os.path.exists(filename):
        os.remove(filename)
        
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ تم إلغاء العملية.")
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك!\nأرسل رابط فيديو مباشر (MP4/MKV) للبدء.")

# ==========================================
# تشغيل التطبيق
# ==========================================
def main():
    if TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("Error: Please set your bot token.")
        return

    # تشغيل السيرفر الوهمي في Thread منفصل ليعمل بالتوازي مع البوت
    threading.Thread(target=start_web_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url)],
        states={
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    print("🤖 Bot is runnning...")
    
    # استخدام drop_pending_updates لتجاهل الرسائل القديمة عند إعادة التشغيل
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
