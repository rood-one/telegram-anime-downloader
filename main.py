import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from urllib.parse import urlparse

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MAX_SIZE_MB = 45  # تخفيض الحد الأقصى لحجم الملف

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رابط الحلقة 👇")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    await update.message.reply_text("✅ تم استلام الرابط، جاري المعالجة...")

    try:
        # التحقق من صحة الرابط
        parsed_url = urlparse(url)
        if not all([parsed_url.scheme, parsed_url.netloc]):
            await update.message.reply_text("❌ رابط غير صالح")
            return

        # الحصول على حجم الملف دون تنزيل كامل
        response = requests.head(url, allow_redirects=True, timeout=10)
        response.raise_for_status()
        
        content_length = response.headers.get('Content-Length')
        if not content_length:
            await update.message.reply_text("❌ لا يمكن تحديد حجم الملف")
            return

        size_mb = int(content_length) / (1024 * 1024)
        filename = os.path.basename(parsed_url.path) or "episode.mkv"

        # إرسال الرابط مباشرة إذا تجاوز الحجم المسموح
        if size_mb > MAX_SIZE_MB:
            await update.message.reply_text(
                f"📦 حجم الملف: {round(size_mb, 2)}MB (يتجاوز الحد {MAX_SIZE_MB}MB)\n"
                f"🔗 استخدم الرابط مباشرة:\n{url}"
            )
            return

        await update.message.reply_text(f"⏳ جاري التحميل ({round(size_mb, 2)}MB)...")
        
        # تنزيل الملف بقطع صغيرة
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=10240):
                if chunk:
                    f.write(chunk)
        
        # التحقق من الحجم الفعلي بعد التنزيل
        actual_size = os.path.getsize(filename) / (1024 * 1024)
        if actual_size > MAX_SIZE_MB:
            await update.message.reply_text(
                f"⚠️ تجاوز الحد بعد التنزيل ({round(actual_size, 2)}MB)\n"
                f"🔗 استخدم الرابط مباشرة:\n{url}"
            )
            os.remove(filename)
            return

        # إرسال الملف مع معالجة الأخطاء
        try:
            await update.message.reply_video(
                video=open(filename, 'rb'),
                supports_streaming=True,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=60,
                pool_timeout=60
            )
        except Exception as send_error:
            await update.message.reply_text(
                f"❌ فشل الإرسال: {str(send_error)}\n"
                f"🔗 استخدم الرابط مباشرة:\n{url}"
            )
        finally:
            if os.path.exists(filename):
                os.remove(filename)

    except requests.exceptions.RequestException as req_error:
        await update.message.reply_text(f"❌ خطأ في الاتصال: {str(req_error)}")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ غير متوقع: {str(e)}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()
