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
logger = logging.getLogger(__name__)

# --- الحصول على متغيرات البيئة ---
TOKEN = os.getenv("BOT_TOKEN")
PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN_API_KEY")

MAX_DIRECT_SIZE = 45  # الحد الأقصى (MB) للإرسال المباشر عبر تليجرام

# --- تطبيق Flask لإبقاء الخادم نشطًا ---
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

# --- دوال المساعدة (Synchronous) ---
def upload_to_pixeldrain(file_path, filename=None):
    """رفع الملف إلى Pixeldrain"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"محاولة الرفع إلى Pixeldrain (المحاولة {attempt+1})")
            
            headers = {}
            if PIXELDRAIN_API_KEY:
                auth_str = f":{PIXELDRAIN_API_KEY}"
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
            
            if response.status_code in [200, 201]:
                json_response = response.json()
                if json_response.get('success', False):
                    file_id = json_response.get('id')
                    return f"https://pixeldrain.com/u/{file_id}"
                else:
                    logger.error(f"فشل الرفع (success=false): {json_response}")
            else:
                logger.error(f"فشل الرفع (HTTP {response.status_code}): {response.text}")
            
            # إذا لم يكن النجاح 200/201، نجرب مرة أخرى
            if attempt < max_retries - 1:
                time.sleep(5)
                
        except Exception as e:
            logger.error(f"خطأ في الرفع إلى Pixeldrain: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                raise Exception(f"فشل الرفع إلى Pixeldrain بعد {max_retries} محاولات: {str(e)}")
    
    return None

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
            }
            
            if downloaded_size > 0:
                headers['Range'] = f'bytes={downloaded_size}-'
            
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
                
                return downloaded_size / (1024 * 1024)  # Return size in MB
                
        except Exception as e:
            logger.error(f"خطأ في التحميل: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                raise

def get_file_size(url):
    """الحصول على حجم الملف"""
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        # إذا فشل head نجرب get مع range
        if 'Content-Length' not in response.headers:
            response = requests.get(url, headers={'Range': 'bytes=0-1'}, stream=True, timeout=10)
        
        size = int(response.headers.get('Content-Length', 0))
        return size / (1024 * 1024)  # Return size in MB
    except Exception as e:
        logger.error(f"خطأ في الحصول على حجم الملف: {str(e)}")
        return 0

def sanitize_filename(name):
    """تنظيف اسم الملف"""
    return re.sub(r'[^\w\-_\. ]', '', name).strip()

# --- دوال المعالجة (Async) ---
async def process_large_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة الملف الكبير (رفعه إلى Pixeldrain)"""
    chat_id = update.message.chat_id
    try:
        status_msg = await context.bot.send_message(chat_id, "⏳ بدأت معالجة الملف الكبير...")
    except Exception as e:
        # إذا فشل إرسال الرسالة، نعيد المحاولة في الرسالة الأصلية
        await update.message.reply_text("⏳ بدأت معالجة الملف الكبير...")
        status_msg = None

    try:
        # إنشاء مجلد مؤقت
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            # 1. التحميل (تشغيل في Thread منفصل لمنع تجميد البوت)
            if status_msg:
                await context.bot.edit_message_text("📥 جاري التحميل إلى الخادم...", chat_id, status_msg.message_id)
            else:
                await update.message.reply_text("📥 جاري التحميل إلى الخادم...")
            
            loop = asyncio.get_running_loop()
            
            # استخدام run_in_executor لتشغيل الدالة الثقيلة
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            # 2. الرفع إلى Pixeldrain (أيضاً في Thread منفصل)
            if status_msg:
                await context.bot.edit_message_text("☁️ جاري الرفع إلى Pixeldrain...", chat_id, status_msg.message_id)
            else:
                await update.message.reply_text("☁️ جاري الرفع إلى Pixeldrain...")
            
            download_link = await loop.run_in_executor(None, upload_to_pixeldrain, file_path, filename)
            
            if not download_link:
                raise Exception("فشل الحصول على رابط من Pixeldrain")

            # 3. إرسال النتيجة
            message_text = (
                f"✅ **تمت العملية بنجاح!**\n\n"
                f"📄 الاسم: `{filename}`\n"
                f"📦 الحجم: `{file_size_mb:.2f} MB`\n"
                f"🔗 الرابط: {download_link}\n"
                f"\n📌 *ملاحظة:* تم الرفع إلى Pixeldrain لأن الملف أكبر من {MAX_DIRECT_SIZE}MB"
            )
            
            if status_msg:
                await context.bot.edit_message_text(
                    message_text,
                    chat_id,
                    status_msg.message_id,
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(message_text, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error processing large file: {e}")
        error_msg = f"❌ حدث خطأ أثناء معالجة الملف الكبير: {str(e)}"
        if status_msg:
            await context.bot.edit_message_text(error_msg, chat_id, status_msg.message_id)
        else:
            await update.message.reply_text(error_msg)

async def process_small_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة الملف الصغير (إرساله مباشرة عبر تليجرام)"""
    chat_id = update.message.chat_id
    try:
        status_msg = await context.bot.send_message(chat_id, "⏬ جاري المعالجة والإرسال المباشر...")
    except Exception as e:
        status_msg = None

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            loop = asyncio.get_running_loop()
            
            if status_msg:
                await context.bot.edit_message_text("📥 جاري التحميل...", chat_id, status_msg.message_id)
            
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            if status_msg:
                await context.bot.edit_message_text("📤 جاري الرفع إلى تيليجرام...", chat_id, status_msg.message_id)
            
            with open(file_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=f"📄 {filename}\n📦 الحجم: {file_size_mb:.2f} MB"
                )
            
            if status_msg:
                await context.bot.delete_message(chat_id, status_msg.message_id)

    except Exception as e:
        logger.error(f"Error processing small file: {e}")
        error_msg = f"❌ حدث خطأ أثناء معالجة الملف الصغير: {str(e)}"
        if status_msg:
            await context.bot.edit_message_text(error_msg, chat_id, status_msg.message_id)
        else:
            await update.message.reply_text(error_msg)

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
    if not episode_name:
        await update.message.reply_text("⚠️ يرجى إرسال اسم صحيح للحلقة.")
        return

    filename = sanitize_filename(episode_name)
    if not filename:
        filename = "video"  # اسم افتراضي
    
    # إضافة امتداد إذا لم يكن موجوداً
    if not filename.endswith(('.mkv', '.mp4', '.avi', '.mov', '.webm')):
        filename += ".mp4"

    url = context.user_data['url']
    del context.user_data['url']  # تنظيف الذاكرة

    try:
        # فحص الحجم
        msg = await update.message.reply_text("🔍 جاري فحص حجم الملف...")
        
        loop = asyncio.get_running_loop()
        size_mb = await loop.run_in_executor(None, get_file_size, url)
        
        await context.bot.edit_message_text(
            f"🔍 تم اكتشاف الملف.\n📦 الحجم التقديري: {size_mb:.2f} MB",
            chat_id=update.message.chat_id,
            message_id=msg.message_id
        )

        # اتخاذ القرار بناءً على الحجم
        if size_mb > MAX_DIRECT_SIZE or size_mb == 0:
            # إذا كان الحجم أكبر من الحد أو غير معروف، نرفع إلى Pixeldrain
            await context.bot.edit_message_text(
                f"📦 الحجم: {size_mb:.2f} MB (أكبر من {MAX_DIRECT_SIZE}MB)\n⏳ جاري الرفع إلى Pixeldrain...",
                chat_id=update.message.chat_id,
                message_id=msg.message_id
            )
            await process_large_file(update, context, url, filename)
        else:
            # إذا كان الحجم صغيراً، نرسله مباشرة
            await context.bot.edit_message_text(
                f"📦 الحجم: {size_mb:.2f} MB (أقل من {MAX_DIRECT_SIZE}MB)\n⏳ جاري الإرسال المباشر...",
                chat_id=update.message.chat_id,
                message_id=msg.message_id
            )
            await process_small_file(update, context, url, filename)
            
    except Exception as e:
        logger.error(f"Error in handle_episode_name: {e}")
        error_msg = f"❌ خطأ في المعالجة: {str(e)}"
        try:
            await context.bot.edit_message_text(
                error_msg,
                chat_id=update.message.chat_id,
                message_id=msg.message_id
            )
        except:
            await update.message.reply_text(error_msg)

# --- معالجة الأمر /start ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /start"""
    welcome_text = (
        "👋 مرحبًا! أنا بوت لتحويل روابط الفيديو إلى ملفات في تليجرام.\n\n"
        "📌 **كيفية الاستخدام:**\n"
        "1. أرسل رابط الفيديو\n"
        "2. أرسل اسم الحلقة\n"
        "3. انتظر حتى يكتمل التحويل\n\n"
        "📦 **ملاحظة:**\n"
        f"- الملفات الأصغر من {MAX_DIRECT_SIZE}MB تُرسل مباشرة\n"
        f"- الملفات الأكبر من {MAX_DIRECT_SIZE}MB تُرفع إلى Pixeldrain\n\n"
        "🚀 ابدأ الآن بإرسال رابط الفيديو!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

# --- معالجة الأمر /help ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /help"""
    help_text = (
        "📖 **مساعدة في استخدام البوت:**\n\n"
        "1. أرسل رابط الفيديو (يجب أن يكون رابطًا مباشرًا)\n"
        "2. أرسل اسم الحلقة (مثال: Naruto Episode 1)\n"
        "3. انتظر حتى يكتمل التحميل والتحويل\n\n"
        "📌 **ملاحظات مهمة:**\n"
        f"- الحد الأقصى للإرسال المباشر: {MAX_DIRECT_SIZE}MB\n"
        "- الملفات الكبيرة تُرفع تلقائيًا إلى Pixeldrain\n"
        "- تأكد من أن الرابط مباشر وصالح للتحميل\n\n"
        "❓ **مشاكل شائعة:**\n"
        "- إذا لم يعمل الرابط، تأكد أنه رابط مباشر للتحميل\n"
        "- يمكن للبوت التعامل مع معظم صيغ الفيديو (mp4, mkv, etc.)\n"
        "- الإستجابة قد تستغرق وقتًا للملفات الكبيرة"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

def main():
    # تشغيل خادم Flask لإبقاء البوت نشطًا
    keep_alive()
    
    if not TOKEN:
        logger.error("Error: BOT_TOKEN is not set!")
        print("Error: BOT_TOKEN is not set!")
        return
    
    logger.info("Starting bot...")
    
    # بناء التطبيق
    app_bot = ApplicationBuilder().token(TOKEN).build()

    # إضافة معالجات الأوامر
    from telegram.ext import CommandHandler
    app_bot.add_handler(CommandHandler("start", start_command))
    app_bot.add_handler(CommandHandler("help", help_command))
    
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

    # بدء البوت
    logger.info("Bot is running...")
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()