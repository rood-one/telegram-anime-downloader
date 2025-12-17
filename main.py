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
import base64
import asyncio

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

def sanitize_filename(name):
    """تنظيف اسم الملف"""
    return re.sub(r'[^\w\-_\. ]', '', name).strip()

# --- دوال المعالجة (Async) ---
async def process_direct_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة الإرسال المباشر عبر تليجرام"""
    chat_id = update.effective_chat.id
    query = update.callback_query
    
    try:
        await query.answer()
        await query.edit_message_text("📤 تم اختيار الإرسال المباشر عبر تليجرام\n\n⏳ جاري التحميل...")
    except:
        # إذا فشل تحرير الرسالة، نرسل رسالة جديدة
        message = await context.bot.send_message(chat_id, "📤 تم اختيار الإرسال المباشر عبر تليجرام\n\n⏳ جاري التحميل...")
        query = type('obj', (object,), {'message': message})()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            loop = asyncio.get_running_loop()
            await query.edit_message_text("📥 جاري تحميل الملف...")
            
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            # تحقق من حجم الملف
            if file_size_mb > MAX_DIRECT_SIZE:
                await query.edit_message_text(
                    f"❌ **خطأ:** الملف كبير جداً للإرسال المباشر\n\n"
                    f"📦 الحجم: {file_size_mb:.2f} MB\n"
                    f"📊 الحد الأقصى للإرسال المباشر: {MAX_DIRECT_SIZE} MB\n\n"
                    f"⚠️ يرجى إعادة المحاولة واختيار رفع الملف إلى Pixeldrain",
                    parse_mode='Markdown'
                )
                return
            
            await query.edit_message_text("📤 جاري الإرسال إلى تليجرام...")
            
            with open(file_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=f"📄 {filename}\n📦 الحجم: {file_size_mb:.2f} MB"
                )
            
            await query.edit_message_text(f"✅ **تم الإرسال بنجاح!**\n\n📄 الملف: `{filename}`\n📦 الحجم: `{file_size_mb:.2f} MB`", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in direct send: {e}")
        error_msg = f"❌ حدث خطأ أثناء الإرسال المباشر: {str(e)}"
        try:
            await query.edit_message_text(error_msg)
        except:
            await context.bot.send_message(chat_id, error_msg)

async def process_pixeldrain_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة رفع الملف إلى Pixeldrain"""
    chat_id = update.effective_chat.id
    query = update.callback_query
    
    try:
        await query.answer()
        await query.edit_message_text("☁️ تم اختيار رفع الملف إلى Pixeldrain\n\n⏳ جاري التحميل...")
    except:
        message = await context.bot.send_message(chat_id, "☁️ تم اختيار رفع الملف إلى Pixeldrain\n\n⏳ جاري التحميل...")
        query = type('obj', (object,), {'message': message})()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            loop = asyncio.get_running_loop()
            await query.edit_message_text("📥 جاري تحميل الملف من الرابط...")
            
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            await query.edit_message_text("☁️ جاري رفع الملف إلى Pixeldrain...")
            
            download_link = await loop.run_in_executor(None, upload_to_pixeldrain, file_path, filename)
            
            if not download_link:
                raise Exception("فشل الحصول على رابط من Pixeldrain")

            # إرسال النتيجة
            message_text = (
                f"✅ **تم رفع الملف بنجاح!**\n\n"
                f"📄 الاسم: `{filename}`\n"
                f"📦 الحجم: `{file_size_mb:.2f} MB`\n"
                f"🔗 رابط التحميل المباشر:\n{download_link}\n\n"
                f"📌 *ملاحظة:* الرابط صالح لمدة 30 يومًا من آخر تحميل"
            )
            
            await query.edit_message_text(message_text, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in Pixeldrain upload: {e}")
        error_msg = f"❌ حدث خطأ أثناء رفع الملف إلى Pixeldrain: {str(e)}"
        try:
            await query.edit_message_text(error_msg)
        except:
            await context.bot.send_message(chat_id, error_msg)

# --- معالجات التلجرام ---
async def request_episode_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة 1: استلام الرابط وطلب الاسم"""
    url = update.message.text.strip()
    context.user_data['url'] = url
    await update.message.reply_text("📝 أرسل اسم الحلقة الآن (مثال: One Piece 1000):")

async def handle_episode_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة 2: استلام الاسم وعرض خيارات الإرسال"""
    if 'url' not in context.user_data:
        await update.message.reply_text("⚠️ أرسل الرابط أولاً.")
        return

    episode_name = update.message.text.strip()
    if not episode_name:
        await update.message.reply_text("⚠️ يرجى إرسال اسم صحيح للحلقة.")
        return

    filename = sanitize_filename(episode_name)
    if not filename:
        filename = "video"
    
    # إضافة امتداد إذا لم يكن موجوداً
    if not filename.endswith(('.mkv', '.mp4', '.avi', '.mov', '.webm', '.flv', '.wmv')):
        filename += ".mp4"

    url = context.user_data['url']
    
    # حفظ البيانات مؤقتاً
    context.user_data['filename'] = filename
    context.user_data['processing'] = True

    # عرض خيارات الإرسال للمستخدم
    keyboard = [
        [
            InlineKeyboardButton("📤 إرسال مباشر عبر تليجرام", callback_data="direct"),
            InlineKeyboardButton("☁️ رفع إلى Pixeldrain", callback_data="pixeldrain")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📄 **تم تحديد الملف:** `{filename}`\n\n"
        f"📊 **الحد الأقصى للإرسال المباشر:** {MAX_DIRECT_SIZE} MB\n\n"
        f"📍 **اختر طريقة الإرسال:**\n"
        f"• 📤 **إرسال مباشر:** أرسل الملف لك مباشرة عبر تليجرام (للملفات الصغيرة)\n"
        f"• ☁️ **رفع إلى Pixeldrain:** تحصل على رابط تحميل مباشر (للملفات الكبيرة)\n\n"
        f"⚠️ *ملاحظة:* إذا اخترت الإرسال المباشر وكان الملف أكبر من {MAX_DIRECT_SIZE}MB، سيفشل الإرسال",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار المستخدم"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    
    # استخراج البيانات من user_data
    url = context.user_data.get('url')
    filename = context.user_data.get('filename')
    
    if not url or not filename:
        await query.answer("⚠️ انتهت الجلسة، يرجى إعادة العملية من البداية", show_alert=True)
        return
    
    await query.answer()
    
    if query.data == "direct":
        await process_direct_send(update, context, url, filename)
    elif query.data == "pixeldrain":
        await process_pixeldrain_upload(update, context, url, filename)
    
    # تنظيف البيانات بعد المعالجة
    if 'url' in context.user_data:
        del context.user_data['url']
    if 'filename' in context.user_data:
        del context.user_data['filename']

# --- معالجة الأوامر ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /start"""
    welcome_text = (
        "👋 **مرحباً بك في بوت تحويل الروابط!**\n\n"
        "📌 **كيفية الاستخدام:**\n"
        "1. أرسل رابط الفيديو المباشر\n"
        "2. أرسل اسم الحلقة\n"
        "3. اختر طريقة الإرسال\n\n"
        "🔧 **طرق الإرسال المتاحة:**\n"
        "• 📤 **إرسال مباشر:** أرسل الملف لك مباشرة عبر تليجرام\n"
        "• ☁️ **رفع إلى Pixeldrain:** تحصل على رابط تحميل مباشر\n\n"
        f"⚠️ **حدود الإرسال المباشر:** {MAX_DIRECT_SIZE} MB\n\n"
        "🚀 ابدأ الآن بإرسال رابط الفيديو!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /help"""
    help_text = (
        "📖 **مساعدة في استخدام البوت:**\n\n"
        "**خطوات الاستخدام:**\n"
        "1. أرسل رابط الفيديو (يجب أن يكون رابطاً مباشراً للتحميل)\n"
        "2. أرسل اسم الحلقة أو الملف\n"
        "3. اختر طريقة الإرسال:\n"
        "   - 📤 **إرسال مباشر:** للملفات الصغيرة (أقل من {MAX_DIRECT_SIZE}MB)\n"
        "   - ☁️ **رفع إلى Pixeldrain:** للملفات الكبيرة (تحصل على رابط تحميل مباشر)\n\n"
        "**ملاحظات مهمة:**\n"
        "• الروابط يجب أن تكون مباشرة للتحميل\n"
        "• الإرسال المباشر قد يفشل للملفات الكبيرة\n"
        "• روابط Pixeldrain صالحة لمدة 30 يومًا من آخر تحميل\n"
        "• يمكن للبوت التعامل مع معظم صيغ الفيديو\n\n"
        "**مشاكل شائعة:**\n"
        "• إذا فشل الإرسال المباشر: استخدم خيار Pixeldrain\n"
        "• إذا فشل التحميل: تأكد من صحة الرابط\n"
        "• إذا لم يظهر خيار الإرسال: أعد إرسال الرابط"
    ).format(MAX_DIRECT_SIZE=MAX_DIRECT_SIZE)
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /cancel"""
    # تنظيف بيانات المستخدم
    context.user_data.clear()
    await update.message.reply_text("✅ تم إلغاء العملية الحالية. يمكنك البدء من جديد بإرسال رابط.")

# --- دالة لإعادة التعيين ---
async def reset_user_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إعادة تعيين بيانات المستخدم عند إرسال رابط جديد"""
    # تنظيف البيانات القديمة عند بدء عملية جديدة
    if 'url' in context.user_data or 'filename' in context.user_data:
        context.user_data.clear()

def main():
    # تشغيل خادم Flask لإبقاء البوت نشطًا
    keep_alive()
    
    if not TOKEN:
        logger.error("Error: BOT_TOKEN is not set!")
        print("Error: BOT_TOKEN is not set!")
        return
    
    if not PIXELDRAIN_API_KEY:
        logger.warning("PIXELDRAIN_API_KEY is not set. Pixeldrain uploads will be anonymous.")
    
    logger.info("Starting bot...")
    
    # بناء التطبيق
    app_bot = ApplicationBuilder().token(TOKEN).build()

    # إضافة معالجات الأوامر
    from telegram.ext import CommandHandler
    app_bot.add_handler(CommandHandler("start", start_command))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("cancel", cancel_command))
    
    # معالج الروابط (Regex) مع إعادة التعيين
    app_bot.add_handler(MessageHandler(
        filters.Regex(r'^https?://') & ~filters.COMMAND, 
        request_episode_name
    ))
    
    # معالج النصوص (للاسم)
    app_bot.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'^https?://'), 
        handle_episode_name
    ))
    
    # معالج Callback Queries (للأزرار)
    app_bot.add_handler(CallbackQueryHandler(handle_callback))

    # بدء البوت
    logger.info("Bot is running...")
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()