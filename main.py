import os
import requests
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from flask import Flask
import threading
import time
import uuid
import tempfile
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import logging
import mimetypes
import re
import hashlib

# تكوين السجلات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# الحصول على متغيرات البيئة
TOKEN = os.getenv("BOT_TOKEN")
MAX_DIRECT_SIZE = 45  # الحد الأقصى لإرسال الملفات مباشرة عبر التلجرام (MB)

# إعدادات Google Drive
SERVICE_ACCOUNT_JSON = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')

# تطبيق Flask لإبقاء الخادم نشطًا
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """تشغيل خادم Flask في خيط منفصل"""
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

def upload_to_gdrive(file_path, filename):
    """رفع الملف إلى Google Drive وإرجاع رابط تحميل مباشر"""
    try:
        logger.info(f"بدء رفع الملف إلى Google Drive: {filename}")
        
        # تحويل بيانات حساب الخدمة من JSON إلى قاموس
        service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
        
        # إنشاء بيانات الاعتماد
        creds = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        
        # بناء خدمة Google Drive
        drive_service = build('drive', 'v3', credentials=creds)
        
        # إنشاء بيانات تعريف الملف
        file_metadata = {
            'name': filename,
            'parents': [GOOGLE_DRIVE_FOLDER_ID.strip()]  # التأكد من إزالة أي مسافات
        }
        
        # إنشاء وسائط الرفع
        media = MediaFileUpload(
            file_path,
            mimetype=mimetypes.guess_type(filename)[0] or 'application/octet-stream',
            resumable=True
        )
        
        # رفع الملف
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webContentLink'
        ).execute()
        
        logger.info(f"تم رفع الملف بنجاح: {file['id']}")
        
        # جعل الملف عامًا للقراءة
        drive_service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        # إرجاع رابط التحميل المباشر
        download_link = file['webContentLink'].replace('&export=download', '')
        logger.info(f"تم إنشاء رابط التحميل: {download_link}")
        
        return download_link
    
    except Exception as e:
        logger.error(f"فشل الرفع إلى Google Drive: {str(e)}")
        raise Exception(f"فشل الرفع إلى Google Drive: {str(e)}")

def download_file(url, file_path):
    """تحميل الملف من الرابط وحفظه في مسار محدد"""
    try:
        logger.info(f"بدء تحميل الملف: {url}")
        
        # إضافة رأس المستخدم لتجنب حظر الطلبات
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
            'Connection': 'keep-alive'
        }
        
        # تحميل الملف
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        
        # الحصول على حجم الملف من الرأس
        file_size = int(response.headers.get('Content-Length', 0))
        size_mb = file_size / (1024 * 1024)
        logger.info(f"حجم الملف: {size_mb:.2f} MB")
        
        # كتابة الملف
        downloaded = 0
        start_time = time.time()
        
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    # تسجيل التقدم كل 5MB
                    if downloaded % (5 * 1024 * 1024) == 0:
                        elapsed = time.time() - start_time
                        speed = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                        logger.info(
                            f"تم تحميل: {downloaded/(1024*1024):.2f}MB / {size_mb:.2f}MB | "
                            f"السرعة: {speed:.2f}MB/s"
                        )
        
        logger.info(f"تم تحميل الملف بنجاح: {file_path}")
        return size_mb
    
    except Exception as e:
        logger.error(f"فشل تحميل الملف: {str(e)}")
        raise Exception(f"فشل تحميل الملف: {str(e)}")

def generate_filename(url):
    """إنشاء اسم ملف قصير وفريد باستخدام تجزئة الرابط"""
    try:
        # إنشاء تجزئة للرابط لتقصير الاسم
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        return f"anime_{url_hash}.mkv"
    except:
        return f"anime_{int(time.time())}_{uuid.uuid4().hex[:6]}.mkv"

def get_file_size(url):
    """الحصول على حجم الملف باستخدام طريقة GET بدلاً من HEAD"""
    try:
        # إضافة رأس المستخدم لتجنب حظر الطلبات
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Range': 'bytes=0-1'  # طلب جزء صغير فقط للحصول على الرأس
        }
        
        # استخدام GET مع نطاق محدود للحصول على حجم الملف
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        
        # الحصول على حجم الملف من الرأس
        content_range = response.headers.get('Content-Range')
        if content_range:
            # تنسيق Content-Range: bytes 0-1/123456
            file_size = int(content_range.split('/')[1])
        else:
            # إذا لم يكن هناك Content-Range، استخدم Content-Length
            file_size = int(response.headers.get('Content-Length', 0))
        
        return file_size
    
    except Exception as e:
        logger.error(f"فشل الحصول على حجم الملف: {str(e)}")
        raise Exception(f"فشل الحصول على حجم الملف: {str(e)}")

async def process_large_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """معالجة الملفات الكبيرة (تحميل + رفع إلى Google Drive)"""
    chat_id = update.message.chat_id
    message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ بدأت عملية تحميل الحلقة الكبيرة..."
    )
    
    try:
        # إنشاء اسم فريد وقصير للملف
        filename = generate_filename(url)
        
        # إنشاء مجلد مؤقت
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            # تحميل الملف
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text="📥 جاري تحميل الحلقة من السيرفر..."
            )
            file_size_mb = download_file(url, file_path)
            
            # رفع الملف إلى Google Drive
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text="☁️ جاري رفع الحلقة إلى Google Drive..."
            )
            download_link = upload_to_gdrive(file_path, filename)
            
            # إرسال رابط التحميل
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text=f"✅ تم الرفع بنجاح!\n"
                     f"📦 حجم الملف: {file_size_mb:.1f} ميجابايت\n\n"
                     f"🔗 رابط التحميل الدائم:\n{download_link}\n\n"
                     f"يمكنك تنزيل الحلقة في أي وقت تناسبك"
            )
    
    except Exception as e:
        logger.error(f"خطأ في معالجة الملف الكبير: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message.message_id,
            text=f"❌ حدث خطأ أثناء المعالجة: {str(e)}"
        )

async def process_small_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """معالجة الملفات الصغيرة (إرسال مباشر عبر التلجرام)"""
    chat_id = update.message.chat_id
    message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏬ جاري تحميل وإرسال الحلقة مباشرةً..."
    )
    
    try:
        # إنشاء اسم فريد وقصير للملف
        filename = generate_filename(url)
        
        # إنشاء مجلد مؤقت
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            # تحميل الملف
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text="📥 جاري تحميل الحلقة..."
            )
            file_size_mb = download_file(url, file_path)
            
            # إرسال الملف
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text="📤 جاري إرسال الحلقة مباشرةً..."
            )
            await context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(open(file_path, 'rb'), filename=filename),
                caption=f"📦 حجم الملف: {file_size_mb:.1f} ميجابايت"
            )
            
            # حذف الرسالة الأصلية
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message.message_id
            )
    
    except Exception as e:
        logger.error(f"خطأ في معالجة الملف الصغير: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message.message_id,
            text=f"❌ حدث خطأ أثناء المعالجة: {str(e)}"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل الواردة"""
    if not update.message or not update.message.text:
        return
    
    url = update.message.text.strip()
    
    # التحقق من صحة الرابط
    if not url.startswith('http'):
        await update.message.reply_text("⚠️ الرابط غير صالح. أرسل رابط تحميل مباشر فقط.")
        return
    
    try:
        # الحصول على حجم الملف باستخدام الطريقة الجديدة
        file_size = get_file_size(url)
        size_mb = file_size / (1024 * 1024)
        
        # إعلام المستخدم بحجم الملف
        await update.message.reply_text(
            f"🔍 تم التعرف على حلقة الأنمي\n"
            f"📦 الحجم: {size_mb:.1f} ميجابايت\n\n"
            f"⏳ جاري بدء العملية..."
        )
        
        # تحديد طريقة المعالجة بناءً على حجم الملف
        if size_mb <= MAX_DIRECT_SIZE:
            await process_small_file(update, context, url)
        else:
            await process_large_file(update, context, url)
    
    except requests.RequestException as e:
        logger.error(f"خطأ في الاتصال: {str(e)}")
        await update.message.reply_text(f"❌ خطأ في الاتصال بالخادم: {str(e)}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع: {str(e)}")
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {str(e)}")

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    # تشغيل خادم Flask لإبقاء التطبيق نشطًا
    keep_alive()
    
    # إنشاء وتشغيل بوت التلجرام
    logger.info("جاري تشغيل بوت التلجرام...")
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("البوت يعمل الآن!")
    app_bot.run_polling()

if __name__ == '__main__':
    main()
