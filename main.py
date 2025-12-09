import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from flask import Flask
import threading
import time
import tempfile
import logging
import re
import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor

# --- تكوين السجلات ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)  # تصحيح name إلى __name__

# --- الحصول على متغيرات البيئة ---
TOKEN = os.getenv("BOT_TOKEN")
# تأكد من وضع مفتاح Pixeldrain في متغيرات البيئة باسم PIXELDRAIN_API_KEY

MAX_DIRECT_SIZE = 45  # الحد الأقصى (MB)

# --- تطبيق Flask لإبقاء الخادم نشطًا ---
app = Flask(__name__)  # تصحيح name إلى __name__

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

# --- دوال المساعدة (Synchronous) ---
# ستبقى هذه الدوال كما هي ولكن سيتم استدعاؤها بطريقة لا توقف البوت

def upload_to_pixeldrain(file_path, filename=None):
    """رفع الملف إلى Pixeldrain"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"محاولة الرفع إلى Pixeldrain (المحاولة {attempt+1})")
            
            headers = {}
            api_key = os.getenv("PIXELDRAIN_API_KEY")
            if api_key:
                auth_str = f":{api_key}"
                b64_auth = base64.b64encode(auth_str.encode()).decode()
                headers["Authorization"] = f"Basic {b64_auth}"
            
            with open(file_path, 'rb') as f:
                files = {'file': (filename, f)} if filename else {'file': f}
                response = requests.post(
                    'https://pixeldrain.com/api/file',
                    files=files,
                    headers=headers,
                    timeout=600  # زيادة المهلة للملفات الكبيرة
                )
            
            if response.status_code == 201 or response.status_code == 200:
                json_response = response.json()
                if json_response.get('success', False):
                    file_id = json_response.get('id')
                    return f"https://pixeldrain.com/u/{file_id}" # تم تعديل الرابط ليكون رابط مشاهدة/تحميل مباشر
                
            logger.error(f"فشل الرفع: {response.text}")
            response.raise_for_status()
            
        except Exception as e:
            logger.error(f"خطأ في الرفع: {str(e)}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(5)

def download_file(url, file_path):
    """تحميل الملف"""
    max_retries = 3
    downloaded_size = 0
    
    # إذا كان الملف موجودًا جزئيًا (للاستئناف)
    if os.path.exists(file_path):
        downloaded_size = os.path.getsize(file_path)

    for attempt in range(max_retries):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0',
                'Range': f'bytes={downloaded_size}-'
            }
            
            # stream=True مهم جداً لعدم تحميل الملف في الرام
            with requests.get(url, headers=headers, stream=True, timeout=60) as response:
                response.raise_for_status()
                
                total_size = int(response.headers.get('Content-Length', 0)) + downloaded_size
                mode = 'ab' if downloaded_size > 0 else 'wb'
                
                with open(file_path, mode) as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                
                return downloaded_size / (1024 * 1024) # Return size in MB
                
        except Exception as e:
            logger.error(f"خطأ في التحميل: {str(e)}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(5)

def get_file_size(url):
    """الحصول على حجم الملف"""
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        # إذا فشل head نجرب get مع range
        if 'Content-Length' not in response.headers:
             response = requests.get(url, headers={'Range': 'bytes=0-1'}, stream=True, timeout=10)
        
        size = int(response.headers.get('Content-Length', 0))
        return size
    except:
        return 0

def sanitize_filename(name):
    return re.sub(r'[^\w\-_\. ]', '', name).strip()

# --- دوال المعالجة (Async) ---

async def process_large_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    chat_id = update.message.chat_id
    status_msg = await context.bot.send_message(chat_id, "⏳ بدأت معالجة الملف الكبير...")

    try:
        # إنشاء مجلد مؤقت
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            # 1. التحميل (تشغيل في Thread منفصل لمنع تجميد البوت)
            await context.bot.edit_message_text("📥 جاري التحميل إلى الخادم...", chat_id, status_msg.message_id)
            loop = asyncio.get_running_loop()
            
            # استخدام run_in_executor لتشغيل الدالة الثقيلة
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            # 2. الرفع إلى Pixeldrain (أيضاً في Thread منفصل)
            await context.bot.edit_message_text("☁️ جاري الرفع إلى Pixeldrain...", chat_id, status_msg.message_id)
            
            download_link = await loop.run_in_executor(None, upload_to_pixeldrain, file_path, filename)
            
            if not download_link:
                raise Exception("فشل الحصول على رابط من Pixeldrain")

            # 3. إرسال النتيجة
            await context.bot.edit_message_text(
                f"✅ **تمت العملية بنجاح!**\n\n"
                f"📄 الاسم: `{filename}`\n"
                f"📦 الحجم: `{file_size_mb:.2f} MB`\n"
                f"🔗 الرابط: {download_link}",
                chat_id,
                status_msg.message_id,
                parse_mode='Markdown'
            )

    except Exception as e:
        logger.error(f"Error: {e}")
        await context.bot.edit_message_text(f"❌ حدث خطأ: {str(e)}", chat_id, status_msg.message_id)

async def process_small_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    chat_id = update.message.chat_id
    status_msg = await context.bot.send_message(chat_id, "⏬ جاري المعالجة والإرسال المباشر...")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, download_file, url, file_path)
            
            await context.bot.edit_message_text("📤 جاري الرفع إلى تيليجرام...", chat_id, status_msg.message_id)
            
            with open(file_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=f"📄 {filename}"
                )
            
            await context.bot.delete_message(chat_id, status_msg.message_id)

    except Exception as e:
        logger.error(f"Error small file: {e}")
        await context.bot.edit_message_text(f"❌ خطأ: {str(e)}", chat_id, status_msg.message_id)

# --- معالجات التلجرام ---

async def request_episode_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة 1: استلام الرابط وطلب الاسم"""
    url = update.message.text.strip()
    context.user_data['url'] = url
    await update.message.reply_text("📝 أرسل اسم الحلقة الآن (مثال: One Piece 1000):")

async def handle_episode_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة 2: استلام الاسم وبدء العمل"""
    if 'url' not in context.user_data:
        await update.message.reply_text("⚠️ أرسل الرابط أولاً.")
        return

    episode_name = update.message.text.strip()
    filename = sanitize_filename(episode_name)
    if not filename.endswith(('.mkv', '.mp4')):
        filename += ".mp4" # افتراض mp4 اذا لم يحدد

    url = context.user_data['url']
    del context.user_data['url'] # تنظيف الذاكرة

    try:
        # فحص الحجم سريعاً (blocking call but fast)
        file_size_bytes = await asyncio.get_running_loop().run_in_executor(None, get_file_size, url)
        size_mb = file_size_bytes / (1024 * 1024)

        msg = await update.message.reply_text(
            f"🔍 تم اكتشاف الملف.\n📦 الحجم التقديري: {size_mb:.2f} MB\n⏳ جاري بدء التحميل..."
        )

        if size_mb > MAX_DIRECT_SIZE or size_mb == 0:
            # إذا كان الحجم 0 (غير معروف) نعامله كملف كبير للأمان
            await process_large_file(update, context, url, filename)
        else:
            await process_small_file(update, context, url, filename)
            
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في التهيئة: {e}")

def main():
    keep_alive()
    
    if not TOKEN:
        print("Error: BOT_TOKEN is not set!")
        return

    logger.info("Bot started...")
    app_bot = ApplicationBuilder().token(TOKEN).build()

    # معالج الروابط (Regex)
    app_bot.add_handler(MessageHandler(
        filters.Regex(r'^https?://') & ~filters.COMMAND, 
        request_episode_name
    ))
    
    # معالج النصوص (للاسم)
    app_bot.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'^https?://'), 
        handle_episode_name
    ))

    app_bot.run_polling()

if __name__ == '__main__':  # تصحيح الاسم هنا
    main()
