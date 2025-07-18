import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from flask import Flask
import threading
import time
import uuid
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import tempfile
import json

TOKEN = os.getenv("BOT_TOKEN")
MAX_DIRECT_SIZE = 45  # MB

# إعدادات Google Drive
SERVICE_ACCOUNT_INFO = json.loads(os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON'))
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    thread = threading.Thread(target=run_flask)
    thread.start()

def upload_to_gdrive(file_path, filename):
    """رفع الملف إلى Google Drive وإرجاع رابط مباشر"""
    try:
        # مصادقة باستخدام حساب الخدمة
        creds = service_account.Credentials.from_service_account_info(
            SERVICE_ACCOUNT_INFO,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        
        drive_service = build('drive', 'v3', credentials=creds)
        
        # إنشاء ملف في Google Drive
        file_metadata = {
            'name': filename,
            'parents': [GOOGLE_DRIVE_FOLDER_ID]
        }
        
        media = MediaFileUpload(file_path, resumable=True)
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink, webContentLink'
        ).execute()
        
        # جعل الملف عاماً للقراءة
        drive_service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        # إرجاع رابط التحميل المباشر
        return file['webContentLink'].replace('&export=download', '')
    
    except Exception as e:
        raise Exception(f"فشل الرفع إلى Google Drive: {str(e)}")

async def download_and_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل الملف ثم رفعه إلى Google Drive"""
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    
    try:
        # الحصول على حجم الملف
        head = requests.head(url, allow_redirects=True)
        file_size = int(head.headers.get('Content-Length', 0))
        size_mb = file_size / (1024 * 1024)
        
        # إنشاء اسم فريد للملف
        filename = f"anime_{int(time.time())}_{uuid.uuid4().hex[:6]}.mkv"
        
        # إعلام المستخدم ببدء التحميل
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📥 جاري تحميل الحلقة ({size_mb:.1f}MB)..."
        )
        
        # تحميل الملف إلى ملف مؤقت
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, filename)
        
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(file_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        
        # إعلام المستخدم ببدء الرفع
        await context.bot.send_message(
            chat_id=chat_id,
            text="☁️ جاري رفع الحلقة إلى Google Drive..."
        )
        
        # رفع الملف إلى Google Drive
        download_link = upload_to_gdrive(file_path, filename)
        
        # إرسال رابط التحميل
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ تم الرفع بنجاح!\n"
                 f"🔗 رابط التحميل:\n{download_link}\n\n"
                 f"يمكنك تنزيل الحلقة في أي وقت"
        )
        
        # تنظيف الملفات المؤقتة
        os.remove(file_path)
        os.rmdir(temp_dir)
        
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ حدث خطأ: {str(e)}"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    url = update.message.text.strip()

    if not url.startswith('http'):
        await update.message.reply_text("⚠️ أرسل لي رابط التحميل المباشر فقط.")
        return

    try:
        # الحصول على حجم الملف
        head = requests.head(url, allow_redirects=True)
        file_size = int(head.headers.get('Content-Length', 0))
        size_mb = file_size / (1024 * 1024)
        
        # حالة الملفات الصغيرة
        if size_mb <= MAX_DIRECT_SIZE:
            # ... نفس كود إرسال الملفات الصغيرة ...
            pass
        # حالة الملفات الكبيرة
        else:
            # بدء عملية التحميل والرفع في خيط منفصل
            threading.Thread(
                target=lambda: asyncio.run(
                    download_and_upload(update, context)
            ).start()
            
            await update.message.reply_text(
                f"📦 الحلقة كبيرة الحجم ({size_mb:.1f}MB)\n"
                "⏳ جاري تحميلها ورفعها إلى Google Drive...\n"
                "سأرسل لك رابط التحميل فور الانتهاء."
            )

    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

# تشغيل الخادم المساعد
keep_alive()

# تشغيل البوت
if __name__ == '__main__':
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app_bot.run_polling()
