import os
import requests
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from flask import Flask, send_from_directory
import threading
import time

TOKEN = os.getenv("BOT_TOKEN")
MAX_DIRECT_SIZE = 45  # الحد الأقصى للإرسال المباشر (MB)

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

@app.route('/files/<filename>')
def serve_file(filename):
    return send_from_directory('files', filename)

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    thread = threading.Thread(target=run_flask)
    thread.start()

async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    url = update.message.text.strip()

    if not url.startswith('http'):
        await update.message.reply_text("أرسل لي رابط التحميل المباشر فقط.")
        return

    try:
        # 1. الحصول على حجم الملف
        head = requests.head(url, allow_redirects=True)
        file_size = int(head.headers.get('Content-Length', 0))
        size_mb = file_size / (1024 * 1024)
        
        filename = os.path.basename(url).split("?")[0] or "episode.mkv"

        # 2. إذا كان الملف صغيراً
        if size_mb <= MAX_DIRECT_SIZE:
            await update.message.reply_text(f"جاري تحميل الحلقة ({size_mb:.1f}MB)...")
            response = requests.get(url, stream=True)
            temp_file = f"temp_{int(time.time())}.mkv"
            
            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
            
            await update.message.reply_document(
                document=InputFile(open(temp_file, 'rb')),
                filename=filename
            )
            os.remove(temp_file)

        # 3. إذا كان الملف كبيراً
        else:
            await update.message.reply_text(f"الحلقة كبيرة ({size_mb:.1f}MB)، جاري التحميل إلى السيرفر...")
            
            # إنشاء مجلد الملفات
            os.makedirs('files', exist_ok=True)
            filepath = os.path.join('files', filename)
            
            # تحميل الملف إلى مجلد files
            response = requests.get(url, stream=True)
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
            
            # إنشاء رابط مباشر
            base_url = os.getenv('RENDER_EXTERNAL_HOSTNAME', 'your-bot.onrender.com')
            file_url = f"https://{base_url}/files/{filename}"
            
            await update.message.reply_text(
                f"تم التحميل بنجاح!\n"
                f"رابط التحميل المباشر:\n{file_url}\n\n"
                "ملاحظة: الرابط صالح لمدة 15 دقيقة فقط."
            )

    except Exception as e:
        await update.message.reply_text(f"حدث خطأ: {str(e)}")

# تشغيل الخادم المساعد
keep_alive()

# تشغيل البوت
if __name__ == '__main__':
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), download_and_send))
    app_bot.run_polling()
