import os
import requests
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from flask import Flask
import threading

TOKEN = os.getenv("7669509172:AAH0C2pYoEpj9rskRj5I3vABd_Hc3KqZ3mE")  # نحصل على التوكن من متغير بيئة

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

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
        filename = "episode.mkv"
        response = requests.get(url, stream=True)
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024*1024):
                if chunk:
                    f.write(chunk)

        await update.message.reply_document(document=InputFile(open(filename, 'rb')), filename=filename)
        os.remove(filename)

    except Exception as e:
        await update.message.reply_text(f"حدث خطأ: {e}")

keep_alive()

app_bot = ApplicationBuilder().token(TOKEN).build()
app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), download_and_send))
app_bot.run_polling()
