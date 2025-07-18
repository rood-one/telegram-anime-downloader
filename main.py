import os
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.constants import ChatAction

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MAX_SIZE_MB = 43

def get_file_size_mb(url: str) -> float:
    try:
        response = requests.head(url, allow_redirects=True)
        size = int(response.headers.get("Content-Length", 0))
        return size / (1024 * 1024)
    except Exception:
        return 0

def download_file(url: str, file_path: str):
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(file_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

def handle_start(update: Update, context: CallbackContext):
    update.message.reply_text("مرحبًا! أرسل لي رابط الحلقة وسأتولى الباقي 🎬")

def handle_url(update: Update, context: CallbackContext):
    url = update.message.text.strip()
    chat_id = update.effective_chat.id

    update.message.reply_text("طلبك قيد المعالجة... ⏳")
    context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        size_mb = get_file_size_mb(url)

        if size_mb == 0:
            update.message.reply_text("تعذر التحقق من حجم الملف أو الرابط غير صالح ❌")
            return

        if size_mb <= MAX_SIZE_MB:
            filename = "episode.mkv"
            download_file(url, filename)
            context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
            context.bot.send_video(chat_id=chat_id, video=open(filename, "rb"), caption=f"📦 الحجم: {size_mb:.2f}MB")
            os.remove(filename)
        else:
            update.message.reply_text(
                f"⚠️ حجم الحلقة كبير ({size_mb:.2f}MB) ولا يمكن إرسالها عبر Telegram.\n"
                f"🔗 رابط التحميل: {url}"
            )

    except Exception as e:
        update.message.reply_text(f"حدث خطأ أثناء المعالجة ⚠️\n{e}")

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", handle_start))
    dp.add_handler(CommandHandler("help", handle_start))
    dp.add_handler(CommandHandler("episode", handle_url))
    dp.add_handler(CommandHandler("link", handle_url))
    dp.add_handler(CommandHandler("video", handle_url))

    # التعامل مع أي رسالة كرابط
    dp.add_handler(CommandHandler("", handle_url))  # احتياطي
    dp.add_handler(CommandHandler("text", handle_url))
    dp.add_handler(CommandHandler("url", handle_url))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
