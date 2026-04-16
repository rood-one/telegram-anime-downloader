# -*- coding: utf-8 -*-

import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from flask import Flask
import threading
import time
import tempfile
import logging
import re
import asyncio

# --- تكوين السجلات ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- الحصول على متغيرات البيئة ---
TOKEN = os.getenv("BOT_TOKEN")

MAX_DIRECT_SIZE = 45  # الحد الأقصى (MB) للإرسال المباشر عبر تليجرام

# --- الترويسات المطلوبة لتجاوز حظر 403 ---
# تأكد من أن الـ Referer يطابق الموقع الأصلي الذي يتم السحب منه
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Referer': 'https://av1encodes.com/' 
}

# --- تطبيق Flask لإبقاء الخادم نشطًا ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_flask():
    from waitress import serve
    serve(app, host='0.0.0.0', port=8080)

def keep_alive():
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

# --- دوال المساعدة (Synchronous) ---

def fetch_api_data(api_url):
    """جلب بيانات JSON من رابط الـ API واستخراج رابط التحميل والاسم"""
    try:
        response = requests.get(api_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # استخراج البيانات بناءً على هيكل الـ JSON الذي أرسلته
        download_link = data.get('download_link') or data.get('stream_link')
        file_name = data.get('file_name', 'video_file.mkv')
        
        if not download_link:
            raise ValueError("لم يتم العثور على رابط تحميل في الـ JSON")
            
        return download_link, file_name
    except Exception as e:
        logger.error(f"خطأ في جلب الـ API: {str(e)}")
        raise e

def upload_to_gofile(file_path, filename=None):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"محاولة الرفع إلى Gofile.io (المحاولة {attempt+1})")
            server_response = requests.get('https://api.gofile.io/servers', timeout=10)
            server_response.raise_for_status()
            server_data = server_response.json()
            
            if server_data.get('status') == 'ok':
                server = server_data['data']['servers'][0]['name']
                upload_url = f'https://{server}.gofile.io/contents/uploadfile'
            else:
                upload_url = 'https://store-eu-gra-2.gofile.io/contents/uploadfile'

            with open(file_path, 'rb') as f:
                files = {'file': (filename, f)} if filename else {'file': f}
                response = requests.post(upload_url, files=files, timeout=900)
            
            response.raise_for_status()
            json_response = response.json()
            
            if json_response.get('status') == 'ok':
                return json_response['data']['downloadPage']

        except Exception as e:
            logger.error(f"خطأ في الرفع: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                raise Exception(f"فشل الرفع بعد {max_retries} محاولات.")
    return None

def download_file(url, file_path):
    """تحميل الملف مع تمرير الترويسات لتجاوز 403"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # استخدام HEADERS الموحدة التي تحتوي على Referer
            with requests.get(url, headers=HEADERS, stream=True, timeout=60) as response:
                response.raise_for_status()
                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            return os.path.getsize(file_path) / (1024 * 1024)
        except Exception as e:
            logger.error(f"خطأ في التحميل (المحاولة {attempt+1}): {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                raise Exception(f"فشل التحميل بعد {max_retries} محاولات: {str(e)}")

def sanitize_filename(name):
    name = re.sub(r'[^؀-ۿA-Za-z0-9_\.\-\[\] ]', '', name).strip()
    name = re.sub(r'[ ]+', ' ', name)
    return name if name else "video.mkv"

# --- دوال المعالجة (Async) ---

async def process_direct_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    chat_id = update.effective_chat.id
    query = update.callback_query
    
    await query.edit_message_text("📤 **الإرسال المباشر:** جاري التحميل إلى سيرفر البوت...", parse_mode='Markdown')

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, filename)
        try:
            loop = asyncio.get_running_loop()
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            if file_size_mb > MAX_DIRECT_SIZE:
                error_text = (
                    f"❌ **خطأ:** الملف كبير جداً ({file_size_mb:.2f} MB).\n"
                    f"⚠️ الحد الأقصى لتليجرام هو {MAX_DIRECT_SIZE} MB. استخدم Gofile.io."
                )
                await query.edit_message_text(error_text, parse_mode='Markdown')
                return
            
            await query.edit_message_text("📤 **الإرسال المباشر:** جاري الرفع إلى تليجرام...", parse_mode='Markdown')
            
            with open(file_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=f"📄 {filename}\n📦 الحجم: {file_size_mb:.2f} MB",
                    read_timeout=300,
                    write_timeout=300
                )
            
            await query.edit_message_text(f"✅ **تم الإرسال بنجاح!**", parse_mode='Markdown')

        except Exception as e:
            await query.edit_message_text(f"❌ حدث خطأ: {str(e)}", parse_mode='Markdown')

async def process_gofile_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    query = update.callback_query
    await query.edit_message_text("☁️ **Gofile.io:** جاري التحميل إلى سيرفر البوت...", parse_mode='Markdown')

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, filename)
        try:
            loop = asyncio.get_running_loop()
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            await query.edit_message_text("☁️ **Gofile.io:** جاري الرفع للسحابة...", parse_mode='Markdown')
            download_link = await loop.run_in_executor(None, upload_to_gofile, file_path, filename)
            
            if not download_link:
                raise Exception("فشل الحصول على رابط من Gofile.io")

            message_text = (
                f"✅ **تم الرفع بنجاح!**\n\n"
                f"📄 الاسم: `{filename}`\n"
                f"📦 الحجم: `{file_size_mb:.2f} MB`\n"
                f"🔗 **رابط التحميل:**\n{download_link}"
            )
            await query.edit_message_text(message_text, parse_mode='Markdown', disable_web_page_preview=True)

        except Exception as e:
            await query.edit_message_text(f"❌ حدث خطأ: {str(e)}", parse_mode='Markdown')

# --- معالجات التلجرام ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك!**\n\n"
        "أرسل رابط الـ API مباشرة (مثال: `http://av1please.com/get_ddl/...`)\n"
        "وسأقوم باستخراج الملف وتحميله لك."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_url = update.message.text.strip()
    status_message = await update.message.reply_text("🔄 جاري فحص الرابط وجلب البيانات...", parse_mode='Markdown')
    
    try:
        loop = asyncio.get_running_loop()
        # محاولة جلب الرابط والاسم من الـ API
        download_link, raw_filename = await loop.run_in_executor(None, fetch_api_data, api_url)
        filename = sanitize_filename(raw_filename)
        
        # حفظ البيانات في الجلسة للاستخدام في الأزرار
        context.user_data['url'] = download_link
        context.user_data['filename'] = filename
        
        keyboard = [
            [InlineKeyboardButton("📤 إرسال مباشر", callback_data="direct")],
            [InlineKeyboardButton("☁️ رفع إلى Gofile.io", callback_data="gofile")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_message.edit_text(
            f"✅ **تم جلب البيانات بنجاح!**\n\n"
            f"📄 **الملف:** `{filename}`\n\n"
            f"**اختر طريقة الإرسال:**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        # في حال لم يكن الرابط API أو فشل الجلب، نعود للنظام القديم بسؤاله عن الاسم
        context.user_data['url'] = api_url
        await status_message.edit_text(
            "⚠️ لم أتمكن من جلب الاسم تلقائياً (قد لا يكون رابط API).\n\n"
            "📝 يرجى إرسال اسم الملف الآن (مثال: One Piece 1000)"
        )

async def handle_manual_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'url' not in context.user_data:
        return # تجاهل النصوص العادية إذا لم يكن هناك رابط مسبق

    filename = sanitize_filename(update.message.text.strip())
    if not filename.endswith(('.mkv', '.mp4', '.avi', '.mov')):
        filename += ".mkv" # افتراضي للملفات التي تحملها عادة

    context.user_data['filename'] = filename

    keyboard = [
        [InlineKeyboardButton("📤 إرسال مباشر", callback_data="direct")],
        [InlineKeyboardButton("☁️ رفع إلى Gofile.io", callback_data="gofile")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📄 **الملف:** `{filename}`\n\n"
        f"**اختر طريقة الإرسال:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    url = context.user_data.get('url')
    filename = context.user_data.get('filename')
    
    if not url or not filename:
        await query.edit_message_text("⚠️ انتهت الجلسة، يرجى إرسال الرابط مرة أخرى.")
        return
    
    choice = query.data
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
    # التقاط الروابط (سواء كانت API أو روابط عادية)
    app_bot.add_handler(MessageHandler(filters.Regex(r'^https?://') & ~filters.COMMAND, handle_url))
    # التقاط النصوص العادية (لاستخدامها كاسم ملف إذا فشل الـ API)
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'^https?://'), handle_manual_filename))
    app_bot.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot is running...")
    app_bot.run_polling()

if __name__ == '__main__':
    main()
