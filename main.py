# -*- coding: utf-8 -*-

import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from flask import Flask
import threading
import time
import tempfile
import logging
import re
import asyncio

# --- تكوين السجلات ---
logging.basicConfig(
    format=\'%(asctime)s - %(name)s - %(levelname)s - %(message)s\',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- الحصول على متغيرات البيئة ---
TOKEN = os.getenv("BOT_TOKEN")

MAX_DIRECT_SIZE = 45  # الحد الأقصى (MB) للإرسال المباشر عبر تليجرام

# --- تطبيق Flask لإبقاء الخادم نشطًا (مهم لمنصة Render) ---
app = Flask(__name__)

@app.route(\'/\')
def home():
    return "Bot is alive and running!"

def run_flask():
    # تشغيل خادم ويب خفيف لاستهلاك أقل قدر من الموارد
    from waitress import serve
    serve(app, host=\'0.0.0.0\', port=8080)

def keep_alive():
    """تشغيل خادم Flask في خيط منفصل"""
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

# --- دوال المساعدة (Synchronous) ---

def upload_to_gofile(file_path, filename=None):
    """رفع الملف إلى Gofile.io مع استهلاك ذاكرة منخفض"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"محاولة الرفع إلى Gofile.io (المحاولة {attempt+1})")
            
            # 1. الحصول على أفضل خادم متاح
            server_response = requests.get(\'https://api.gofile.io/servers\', timeout=10)
            server_response.raise_for_status()
            server_data = server_response.json()
            
            if server_data.get(\'status\') == \'ok\':
                server = server_data[\'data\'][\'servers\'][0][\'name\]
                upload_url = f\'https://{server}.gofile.io/contents/uploadfile\'
            else:
                # استخدام خادم احتياطي في حالة فشل الحصول على قائمة الخوادم
                logger.warning("فشل الحصول على خادم تلقائي، سيتم استخدام خادم احتياطي.")
                upload_url = \'https://store-eu-gra-2.gofile.io/contents/uploadfile\'

            # 2. رفع الملف (يتم بثه من القرص مباشرة لتقليل استهلاك الذاكرة)
            with open(file_path, \'rb\') as f:
                files = {\'file\': (filename, f)} if filename else {\'file\': f}
                response = requests.post(
                    upload_url,
                    files=files,
                    timeout=900  # 15 دقيقة للملفات الكبيرة
                )
            
            response.raise_for_status()
            json_response = response.json()
            
            if json_response.get(\'status\') == \'ok\':
                download_page = json_response[\'data\'][\'downloadPage\]
                return download_page
            else:
                logger.error(f"فشل الرفع (Gofile API Error): {json_response}")

        except Exception as e:
            logger.error(f"خطأ في الرفع إلى Gofile.io: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                raise Exception(f"فشل الرفع إلى Gofile.io بعد {max_retries} محاولات: {str(e)}")
    
    return None

def download_file(url, file_path):
    """تحميل الملف بكفاءة عالية للذاكرة"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            headers = {\'User-Agent\': \'Mozilla/5.0\'}
            
            # stream=True هو مفتاح عدم تحميل الملف بالكامل في الذاكرة
            with requests.get(url, headers=headers, stream=True, timeout=60) as response:
                response.raise_for_status()
                
                with open(file_path, \'wb\') as f:
                    # كتابة الملف على شكل أجزاء صغيرة (8KB) مباشرة إلى القرص
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            return file_size_mb
                
        except Exception as e:
            logger.error(f"خطأ في التحميل (المحاولة {attempt+1}): {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                raise

def sanitize_filename(name):
    """تنظيف اسم الملف للسماح بالعربية والإنجليزية بأمان"""
    # إزالة أي حروف غير مسموح بها (غير الحروف والأرقام والنقاط والشرطات)
    name = re.sub(r\'[^؀-ۿA-Za-z0-9_\.\- ]\', \'\', name).strip()
    # استبدال المسافات المتعددة بمسافة واحدة
    name = re.sub(r\'[ ]+\', \' \', name)
    return name if name else "video"

# --- دوال المعالجة (Async) ---

async def process_direct_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة الإرسال المباشر عبر تليجرام"""
    chat_id = update.effective_chat.id
    query = update.callback_query
    
    try:
        await query.edit_message_text("📤 **الإرسال المباشر:** جاري التحميل...", parse_mode=\'Markdown\')
    except Exception as e:
        logger.warning(f"فشل تعديل الرسالة: {e}")
        await context.bot.send_message(chat_id, "📤 **الإرسال المباشر:** جاري التحميل...", parse_mode=\'Markdown\')

    # استخدام مجلد مؤقت يضمن الحذف التلقائي للملف بعد الانتهاء
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, filename)
        try:
            loop = asyncio.get_running_loop()
            
            # تشغيل دالة التحميل في خيط منفصل لتجنب حظر البوت
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            if file_size_mb > MAX_DIRECT_SIZE:
                error_text = (
                    f"❌ **خطأ:** الملف كبير جداً ({file_size_mb:.2f} MB) للإرسال المباشر.\n"
                    f"⚠️ الحد الأقصى هو {MAX_DIRECT_SIZE} MB. يرجى استخدام خيار Gofile.io."
                )
                await query.edit_message_text(error_text, parse_mode=\'Markdown\')
                return
            
            await query.edit_message_text("📤 **الإرسال المباشر:** جاري الرفع إلى تليجرام...", parse_mode=\'Markdown\')
            
            with open(file_path, \'rb\') as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=f"📄 {filename}\n📦 الحجم: {file_size_mb:.2f} MB"
                )
            
            await query.edit_message_text(f"✅ **تم الإرسال بنجاح!**", parse_mode=\'Markdown\')

        except Exception as e:
            logger.error(f"خطأ في الإرسال المباشر: {e}")
            await query.edit_message_text(f"❌ حدث خطأ: {str(e)}", parse_mode=\'Markdown\')

async def process_gofile_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة رفع الملف إلى Gofile.io"""
    chat_id = update.effective_chat.id
    query = update.callback_query
    
    try:
        await query.edit_message_text("☁️ **Gofile.io:** جاري التحميل...", parse_mode=\'Markdown\')
    except Exception as e:
        logger.warning(f"فشل تعديل الرسالة: {e}")
        await context.bot.send_message(chat_id, "☁️ **Gofile.io:** جاري التحميل...", parse_mode=\'Markdown\')

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, filename)
        try:
            loop = asyncio.get_running_loop()
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            await query.edit_message_text("☁️ **Gofile.io:** جاري الرفع...", parse_mode=\'Markdown\')
            
            download_link = await loop.run_in_executor(None, upload_to_gofile, file_path, filename)
            
            if not download_link:
                raise Exception("فشل الحصول على رابط من Gofile.io")

            message_text = (
                f"✅ **تم الرفع بنجاح!**\n\n"
                f"📄 الاسم: `{filename}`\n"
                f"📦 الحجم: `{file_size_mb:.2f} MB`\n"
                f"🔗 **رابط التحميل:**\n{download_link}"
            )
            await query.edit_message_text(message_text, parse_mode=\'Markdown\', disable_web_page_preview=True)

        except Exception as e:
            logger.error(f"خطأ في الرفع إلى Gofile.io: {e}")
            await query.edit_message_text(f"❌ حدث خطأ: {str(e)}", parse_mode=\'Markdown\')

# --- معالجات التلجرام ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك!**\n\n"
        "أرسل رابط فيديو مباشر، ثم أرسل اسمه، وسأقوم بمعالجته لك."
    )
    await update.message.reply_text(welcome_text, parse_mode=\'Markdown\')

async def request_episode_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear() # بدء جلسة جديدة
    context.user_data[\'url\] = update.message.text.strip()
    await update.message.reply_text("📝 الآن أرسل اسم الملف (مثال: One Piece 1000)")

async def handle_episode_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if \'url\' not in context.user_data:
        await update.message.reply_text("⚠️ أرسل الرابط أولاً.")
        return

    filename = sanitize_filename(update.message.text.strip())
    if not filename.endswith(('.mkv', '.mp4', '.avi', '.mov')):
        filename += ".mp4"

    context.user_data[\'filename\] = filename

    keyboard = [
        [InlineKeyboardButton("📤 إرسال مباشر", callback_data="direct")],
        [InlineKeyboardButton("☁️ رفع إلى Gofile.io", callback_data="gofile")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📄 **الملف:** `{filename}`\n\n"
        f"**اختر طريقة الإرسال:**",
        reply_markup=reply_markup,
        parse_mode=\'Markdown\'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    url = context.user_data.get(\'url\')
    filename = context.user_data.get(\'filename\')
    
    if not url or not filename:
        await query.edit_message_text("⚠️ انتهت الجلسة، يرجى إرسال الرابط مرة أخرى.")
        return
    
    choice = query.data
    # تعطيل الأزرار بعد الاختيار
    await query.edit_message_reply_markup(reply_markup=None)

    if choice == "direct":
        await process_direct_send(update, context, url, filename)
    elif choice == "gofile":
        await process_gofile_upload(update, context, url, filename)
    
    context.user_data.clear()

def main():
    keep_alive()
    
    if not TOKEN:
        logger.error("Error: BOT_TOKEN is not set!")
        return
    
    logger.info("Starting bot...")
    
    app_bot = ApplicationBuilder().token(TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start_command))
    app_bot.add_handler(MessageHandler(filters.Regex(r\'^https?://\') & ~filters.COMMAND, request_episode_name))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r\'^https?://\'), handle_episode_name))
    app_bot.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot is running...")
    app_bot.run_polling()

if __name__ == \'__main__\':
    main()
