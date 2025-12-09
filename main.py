import os
import requests
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    CommandHandler,
    filters
)

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(TOKEN)

ASK_TITLE = 1

DOWNLOAD_CHUNK = 1024 * 1024   # 1MB
TELEGRAM_LIMIT = 46 * 1024 * 1024  # 46MB

# =============================
# STREAM DOWNLOAD (RAM SAFE)
# =============================
def stream_download(url, out_path):
    with requests.get(url, stream=True, timeout=20) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK):
                if chunk:
                    f.write(chunk)
    return os.path.getsize(out_path)

# =============================
# GOFILE UPLOAD
# =============================
def upload_to_gofile(file_path):
    servers = requests.get("https://api.gofile.io/servers").json()
    server = servers["data"]["servers"][0]

    upload_url = f"https://{server}.gofile.io/uploadFile"

    with open(file_path, "rb") as f:
        files = {"file": f}
        r = requests.post(upload_url, files=files)

    data = r.json()
    if data["status"] != "ok":
        raise Exception("GoFile upload failed")

    return data["data"]["downloadPage"]

# ==========================================
# STEP 1 — يستقبل الرابط
# ==========================================
async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    context.user_data["url"] = url

    await update.message.reply_text("📄 اكتب اسم الحلقة الآن:")
    return ASK_TITLE

# ==========================================
# STEP 2 — يستقبل اسم الحلقة
# ==========================================
async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    url = context.user_data["url"]

    safe_title = "".join(c for c in title if c.isalnum() or c in " -_()[]")
    filename = f"{safe_title}.mp4"

    await update.message.reply_text("⏳ جاري تحميل الحلقة...")

    try:
        size = stream_download(url, filename)
    except Exception as e:
        await update.message.reply_text(f"❌ فشل التحميل:\n{e}")
        return ConversationHandler.END

    # إرسال داخل التلغرام مباشرة
    if size < TELEGRAM_LIMIT:
        await update.message.reply_text("📤 جاري إرسال الملف داخل التلغرام...")

        try:
            with open(filename, "rb") as f:
                await bot.send_document(
                    chat_id=update.message.chat_id,
                    document=f,
                    filename=filename,
                    caption=f"🎬 {title}"
                )
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ أثناء الإرسال:\n{e}")

        os.remove(filename)
        return ConversationHandler.END

    # رفع GoFile
    await update.message.reply_text("📤 الملف كبير، جاري رفعه إلى GoFile...")

    try:
        link = upload_to_gofile(filename)
        await update.message.reply_text(
            f"✅ تم الرفع!\n🎬 {title}\n🔗 رابط التحميل:\n{link}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ فشل رفع الملف:\n{e}")

    os.remove(filename)
    return ConversationHandler.END

# ==========================================
# START BOT
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رابط الحلقة للبدء.")

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url)],
        states={
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    app.run_polling()

if __name__ == "__main__":
    main()