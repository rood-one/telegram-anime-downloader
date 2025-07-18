import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")

MAX_SIZE_MB = 43

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رابط الحلقة 👇")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    await update.message.reply_text("✅ تم استلام الرابط، جاري المعالجة...")

    try:
        response = requests.get(url, stream=True)
        size_mb = int(response.headers.get("Content-Length", 0)) / (1024 * 1024)

        if size_mb < MAX_SIZE_MB:
            filename = "episode.mkv"
            with open(filename, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            with open(filename, "rb") as f:
                await update.message.reply_video(video=f)

            os.remove(filename)
        else:
            await update.message.reply_text(f"الحلقة حجمها {round(size_mb, 2)} ميجا، وهي أكبر من {MAX_SIZE_MB} ميجا.\n"
                                            f"هذا رابط التحميل المباشر:\n{url}")

    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()
