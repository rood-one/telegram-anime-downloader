import os
import requests
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from flask import Flask
import threading
import time
import uuid
import tempfile
import logging
import hashlib
import re

# تكوين السجلات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# الحصول على متغيرات البيئة
TOKEN = os.getenv("BOT_TOKEN")
MAX_DIRECT_SIZE = 45  # الحد الأقصى لإرسال الملفات مباشرة عبر التلجرام (MB)

# تطبيق Flask لإبقاء الخادم نشطًا
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """تشغيل خادم Flask في خيط منفصل"""
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

def upload_to_0x0(file_path, filename):
    """رفع الملف إلى 0x0.st وإرجاع رابط تحميل مباشر"""
    try:
        logger.info(f"بدء رفع الملف إلى 0x0.st: {filename}")
        
        # رفع الملف
        with open(file_path, 'rb') as f:
            response = requests.post(
                "https://0x0.st",
                files={"file": (filename, f)}
            )
        
        response.raise_for_status()
        download_link = response.text.strip()
        logger.info(f"تم إنشاء رابط التحميل: {download_link}")
        
        return download_link
    
    except Exception as e:
        logger.error(f"فشل الرفع إلى 0x0.st: {str(e)}")
        raise Exception(f"فشل الرفع إلى 0x0.st: {str(e)}")

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

def sanitize_filename(name):
    """تنظيف اسم الملف وإزالة الأحرف غير المسموح بها"""
    # إزالة أي أحرف غير آمنة في أسماء الملفات
    return re.sub(r'[^\w\-_. ]', '', name).strip()

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

async def process_large_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة الملفات الكبيرة (تحميل + رفع إلى 0x0.st)"""
    chat_id = update.message.chat_id
    message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ بدأت عملية تحميل الحلقة الكبيرة..."
    )
    
    try:
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
            
            # رفع الملف إلى 0x0.st
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text="☁️ جاري رفع الحلقة إلى 0x0.st..."
            )
            download_link = upload_to_0x0(file_path, filename)
            
            # إرسال رابط التحميل
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text=f"✅ تم الرفع بنجاح!\n"
                     f"📦 حجم الملف: {file_size_mb:.1f} ميجابايت\n"
                     f"📄 اسم الحلقة: {filename}\n\n"
                     f"🔗 رابط التحميل:\n{download_link}\n\n"
                     f"ملاحظة: الرابط صالح لمدة 30 يومًا"
            )
    
    except Exception as e:
        logger.error(f"خطأ في معالجة الملف الكبير: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message.message_id,
            text=f"❌ حدث خطأ أثناء المعالجة: {str(e)}"
        )

async def process_small_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة الملفات الصغيرة (إرسال مباشر عبر التلجرام)"""
    chat_id = update.message.chat_id
    message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏬ جاري تحميل وإرسال الحلقة مباشرةً..."
    )
    
    try:
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
                caption=f"📦 حجم الملف: {file_size_mb:.1f} ميجابايت\n📄 اسم الحلقة: {filename}"
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

async def request_episode_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب اسم الحلقة من المستخدم"""
    chat_id = update.message.chat_id
    url = update.message.text.strip()
    
    # حفظ الرابط في سياق المحادثة
    context.user_data['url'] = url
    
    # إرسال رسالة تطلب اسم الحلقة
    await context.bot.send_message(
        chat_id=chat_id,
        text="📝 الرجاء إرسال اسم الحلقة (مثال: One Piece Episode 1000):"
    )

async def handle_episode_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اسم الحلقة الذي أدخله المستخدم"""
    chat_id = update.message.chat_id
    episode_name = update.message.text.strip()
    
    # تنظيف اسم الحلقة
    filename = sanitize_filename(episode_name) + ".mkv"
    
    # استرجاع الرابط من سياق المحادثة
    url = context.user_data.get('url', '')
    
    if not url:
        await update.message.reply_text("❌ لم يتم العثور على رابط الحلقة. يرجى إعادة المحاولة.")
        return
    
    # مسح البيانات المؤقتة
    context.user_data.clear()
    
    try:
        # الحصول على حجم الملف
        file_size = get_file_size(url)
        size_mb = file_size / (1024 * 1024)
        
        # إعلام المستخدم بحجم الملف
        await update.message.reply_text(
            f"🔍 تم التعرف على حلقة الأنمي\n"
            f"📦 الحجم: {size_mb:.1f} ميجابايت\n"
            f"📄 اسم الحلقة: {filename}\n\n"
            f"⏳ جاري بدء العملية..."
        )
        
        # تحديد طريقة المعالجة بناءً على حجم الملف
        if size_mb <= MAX_DIRECT_SIZE:
            await process_small_file(update, context, url, filename)
        else:
            await process_large_file(update, context, url, filename)
    
    except requests.RequestException as e:
        logger.error(f"خطأ في الاتصال: {str(e)}")
        await update.message.reply_text(f"❌ خطأ في الاتصال بالخادم: {str(e)}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع: {str(e)}")
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل الواردة"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    
    # إذا كان النص يحتوي على رابط
    if text.startswith('http'):
        await request_episode_name(update, context)
    else:
        # إذا كان المستخدم يرسل اسم الحلقة فقط
        if 'url' in context.user_data:
            await handle_episode_name(update, context)
        else:
            await update.message.reply_text("⚠️ يرجى إرسال رابط الحلقة أولاً")

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    # تشغيل خادم Flask لإبقاء التطبيق نشطًا
    keep_alive()
    
    # إنشاء وتشغيل بوت التلجرام
    logger.info("جاري تشغيل بوت التلجرام...")
    app_bot = ApplicationBuilder().token(TOKEN).build()
    
    # إضافة معالجين: واحد للروابط وواحد لأسماء الحلقات
    app_bot.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r'^https?://'),
        request_episode_name
    ))
    app_bot.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_episode_name
    ))
    
    logger.info("البوت يعمل الآن!")
    app_bot.run_polling()

if __name__ == '__main__':
    main()
