import os
import requests
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from flask import Flask, send_from_directory
import threading

TOKEN = os.getenv("BOT_TOKEN")

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

@app.route('/files/<filename>')
def serve_file(filename):
    return send_from_directory('files', filename)

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    thread = threading.Thread(target=run)
    thread.start()

async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    url = update.message.text.strip()

    if not url.startswith('http'):
        await update.message.reply_text("أرسل لي رابط التحميل المباشر فقط.")
        return

    try:
        # الخطوة 1: معرفة حجم الملف من الـ HEAD فقط
        head = requests.head(url)
        file_size = int(head.headers.get('Content-Length', 0))
        size_mb = file_size / (1024 * 1024)

        filename = "episode.mkv"

        # أقل من 50 ميجا → نرسلها في تليجرام
        if size_mb <= 44:
            response = requests.get(url, stream=True)
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

            await update.message.reply_document(document=InputFile(open(filename, 'rb')), filename=filename)
            os.remove(filename)

        # أكثر من 50 ميجا → نخزنها ونرسل رابط فقط
        else:
            os.makedirs('files', exist_ok=True)
            filepath = f"files/{filename}"
            response = requests.get(url, stream=True)
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

            base_url = os.getenv('RENDER_EXTERNAL_HOSTNAME') or 'your-bot.onrender.com'
            file_url = f"https://{base_url}/files/{filename}"
            await update.message.reply_text(f"الحلقة أكبر من 50MB، يمكنك تحميلها من هنا:\n{file_url}")

    except Exception as e:
        await update.message.reply_text(f"حدث خطأ: {e}")

keep_alive()

app_bot = ApplicationBuilder().token(TOKEN).build()
app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), download_and_send))
app_bot.run_polling()
