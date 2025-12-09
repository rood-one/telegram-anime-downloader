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
TOKEN = os.getenv("TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")

ASK_TITLE = 1
DOWNLOAD_CHUNK = 1024 * 1024   # 1MB
TELEGRAM_LIMIT = 48 * 1024 * 1024  # 48MB حد أمان للتيليجرام

# ==========================================
# 1. السيرفر الوهمي (لحل مشكلة Render Port)
# ==========================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running 100%!")

def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    print(f"🌍 Web server started on port {port}")
    server.serve_forever()

# ==========================================
# 2. وظائف التحميل والرفع
# ==========================================
def stream_download(url, out_path):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    with requests.get(url, stream=True, timeout=30, headers=headers) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK):
                if chunk:
                    f.write(chunk)
    return os.path.getsize(out_path)

def upload_to_fileio(file_path):
    """
    استخدام file.io كبديل لـ GoFile لأنه أكثر استقراراً مع السيرفرات
    يسمح حتى 2GB للملف الواحد
    """
    url = "https://file.io"
    # expires=1w تعني أن الرابط صالح لمدة أسبوع (أو حتى يتم تحميله مرة واحدة)
    # ملاحظة: الخطة المجانية لـ file.io تحذف الملف تلقائياً بعد أول تحميل (auto-delete)
    
    with open(file_path, "rb") as f:
        files = {"file": f}
        # يمكنك إضافة expires لجعل الرابط يدوم لفترة أطول إذا كان الحساب مدفوعاً،
        # لكن المجاني غالباً يحذف بعد التحميل
        r = requests.post(url, files=files)

    if r.status_code != 200:
        # طباعة الخطأ الفعلي إذا فشل الرفع
        try:
            error_msg = r.json()
        except:
            error_msg = r.text
        raise Exception(f"خطأ من المصدر: {error_msg}")

    data = r.json()
    if not data.get("success"):
        raise Exception("فشلت عملية الرفع لسبب غير معروف")

    return data["link"]

# ==========================================
# 3. مراحل المحادثة (Handlers)
# ==========================================
async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ رابط غير صحيح.")
        return ConversationHandler.END

    context.user_data["url"] = url
    await update.message.reply_text("📄 أرسل اسم الحلقة/الملف:")
    return ASK_TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    url = context.user_data.get("url")
    user_id = update.effective_user.id

    # تنظيف الاسم
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_()[]")
    if not safe_title: safe_title = "video"
    
    # اسم الملف المؤقت
    filename = f"{user_id}_{safe_title}.mp4"

    await update.message.reply_text("⏳ جاري التحميل إلى السيرفر (Render)...")

    try:
        size = stream_download(url, filename)
    except Exception as e:
        await update.message.reply_text(f"❌ فشل التحميل من المصدر:\n{e}")
        if os.path.exists(filename): os.remove(filename)
        return ConversationHandler.END

    # الحالة 1: ملف صغير (أقل من 48 ميجا) -> إرسال مباشر
    if size < TELEGRAM_LIMIT:
        await update.message.reply_text("📤 الحجم مناسب، جاري الرفع لتليجرام...")
        try:
            with open(filename, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.message.chat_id,
                    document=f,
                    filename=f"{safe_title}.mp4",
                    caption=f"🎬 {title}",
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ تيليجرام:\n{e}")
    
    # الحالة 2: ملف كبير -> رفع خارجي
    else:
        file_size_mb = size // (1024 * 1024)
        await update.message.reply_text(f"⚠️ حجم الملف ({file_size_mb}MB) كبير.\n🚀 جاري الرفع إلى سحابة خارجية (File.io)...")
        
        try:
            link = upload_to_fileio(filename)
            await update.message.reply_text(
                f"✅ **تم الرفع بنجاح!**\n\n🎬 {title}\n📦 الحجم: {file_size_mb}MB\n🔗 **رابط التحميل:**\n{link}\n\n⚠️ *ملاحظة: الرابط قد يعمل لمرة واحدة فقط.*",
                parse_mode="Markdown"
            )
        except Exception as e:
            # هنا سنرى رسالة الخطأ الحقيقية بدلاً من json decode error
            await update.message.reply_text(f"❌ فشل الرفع الخارجي:\n{e}")

    # حذف الملف من سيرفر Render لتوفير المساحة
    if os.path.exists(filename):
        os.remove(filename)
        
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ تم الإلغاء.")
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أرسل رابط الفيديو المباشر للبدء.")

# ==========================================
# التشغيل الرئيسي
# ==========================================
def main():
    if "YOUR_TELEGRAM_BOT_TOKEN" in TOKEN:
        print("❌ Error: TOKEN not set.")
        return

    # تشغيل السيرفر الوهمي في الخلفية
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

    print("🤖 Bot started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
