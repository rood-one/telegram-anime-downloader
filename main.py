import os
import requests
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler

TOKEN = os.getenv("BOT_TOKEN")
bot = telegram.Bot(TOKEN)

ASK_TITLE, WAIT_DOWNLOAD = range(2)

DOWNLOAD_CHUNK = 1024 * 1024   # 1MB per chunk
TELEGRAM_LIMIT = 46 * 1024 * 1024  # 46MB limit

# =========================
# STREAM DOWNLOAD
# =========================
def stream_download(url, out_path):
    with requests.get(url, stream=True, timeout=20) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK):
                if chunk:
                    f.write(chunk)
    return os.path.getsize(out_path)

# =========================
# GOFILE UPLOAD
# =========================
def upload_to_gofile(file_path):
    server = requests.get("https://api.gofile.io/servers").json()["data"]["servers"][0]
    upload_url = f"https://{server}.gofile.io/uploadFile"
    with open(file_path, "rb") as f:
        files = {"file": f}
        r = requests.post(upload_url, files=files)
    data = r.json()
    if data["status"] != "ok":
        raise Exception("GoFile upload failed")
    return data["data"]["downloadPage"]

# =========================
# STEP 1 — استلام الرابط
# =========================
def receive_url(update, context):
    url = update.message.text.strip()

    context.user_data["url"] = url

    update.message.reply_text("📄 اكتب اسم الحلقة الآن (مثال: One Piece - 1136)")

    return ASK_TITLE

# =========================
# STEP 2 — استلام اسم الحلقة
# =========================
def receive_title(update, context):
    title = update.message.text.strip()
    url = context.user_data["url"]

    safe_title = "".join(c for c in title if c.isalnum() or c in " -_()[]")  # اسم آمن للملف
    filename = f"{safe_title}.mp4"

    update.message.reply_text("⏳ جاري تحميل الحلقة...")

    # تحميل الملف
    try:
        size = stream_download(url, filename)
    except Exception as e:
        update.message.reply_text(f"❌ فشل التحميل:\n{e}")
        return ConversationHandler.END

    # إرسال مباشرة إذا أقل من 46MB
    if size < TELEGRAM_LIMIT:
        update.message.reply_text("📤 جاري إرسال الملف مباشرة داخل التليجرام...")

        try:
            with open(filename, "rb") as f:
                bot.send_document(
                    chat_id=update.message.chat_id,
                    document=f,
                    filename=filename,
                    caption=f"🎬 {title}"
                )
        except Exception as e:
            update.message.reply_text(f"❌ خطأ أثناء الإرسال:\n{e}")

        os.remove(filename)
        return ConversationHandler.END

    # رفع إلى GoFile
    update.message.reply_text("📤 الملف كبير، جاري رفعه إلى GoFile...")

    try:
        link = upload_to_gofile(filename)
        update.message.reply_text(
            f"✅ تم الرفع!\n🎬 اسم الحلقة: {title}\n🔗 رابط التحميل:\n{link}"
        )
    except Exception as e:
        update.message.reply_text(f"❌ فشل الرفع:\n{e}")

    os.remove(filename)
    return ConversationHandler.END

# =========================
# BOT SETUP
# =========================
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    conv = ConversationHandler(
        entry_points=[MessageHandler(Filters.text & ~Filters.command, receive_url)],
        states={
            ASK_TITLE: [MessageHandler(Filters.text & ~Filters.command, receive_title)]
        },
        fallbacks=[]
    )

    dp.add_handler(conv)

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()