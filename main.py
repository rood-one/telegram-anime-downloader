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
import json

# --- تكوين السجلات ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- الحصول على متغيرات البيئة ---
TOKEN = os.getenv("BOT_TOKEN")

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
def upload_to_fileio(file_path, filename=None):
    """رفع الملف إلى file.io (بديل لـ Pixeldrain)"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"محاولة الرفع إلى file.io (المحاولة {attempt+1})")
            
            if filename is None:
                filename = os.path.basename(file_path)
            
            with open(file_path, 'rb') as f:
                files = {'file': (filename, f)}
                response = requests.post(
                    'https://file.io',
                    files=files,
                    timeout=300
                )
            
            if response.status_code == 200:
                json_response = response.json()
                if json_response.get('success', False):
                    download_link = json_response.get('link')
                    return download_link
                else:
                    logger.error(f"فشل الرفع إلى file.io: {json_response}")
            else:
                logger.error(f"فشل الرفع (HTTP {response.status_code}): {response.text}")
            
            if attempt < max_retries - 1:
                time.sleep(3)
                
        except Exception as e:
            logger.error(f"خطأ في الرفع إلى file.io: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(3)
            else:
                # جرب خدمة بديلة إذا فشل file.io
                try:
                    return upload_to_anonfiles(file_path, filename)
                except Exception as e2:
                    raise Exception(f"فشل الرفع بعد {max_retries} محاولات: {str(e)} | البديل: {str(e2)}")
    
    return None

def upload_to_anonfiles(file_path, filename=None):
    """رفع الملف إلى anonfiles.com (خدمة بديلة)"""
    try:
        logger.info("محاولة الرفع إلى anonfiles.com")
        
        if filename is None:
            filename = os.path.basename(file_path)
        
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f)}
            response = requests.post(
                'https://api.anonfiles.com/upload',
                files=files,
                timeout=300
            )
        
        if response.status_code == 200:
            json_response = response.json()
            if json_response.get('status', False):
                download_link = json_response['data']['file']['url']['full']
                return download_link
        
        logger.error(f"فشل الرفع إلى anonfiles: {response.text}")
        raise Exception("فشل الرفع إلى anonfiles")
        
    except Exception as e:
        logger.error(f"خطأ في الرفع إلى anonfiles: {str(e)}")
        raise

def upload_to_transfersh(file_path, filename=None):
    """رفع الملف إلى transfer.sh (خدمة أخرى)"""
    try:
        logger.info("محاولة الرفع إلى transfer.sh")
        
        if filename is None:
            filename = os.path.basename(file_path)
        
        with open(file_path, 'rb') as f:
            response = requests.put(
                f'https://transfer.sh/{filename}',
                data=f,
                headers={'Max-Days': '7'},
                timeout=300
            )
        
        if response.status_code == 200:
            download_link = response.text.strip()
            return download_link
        
        logger.error(f"فشل الرفع إلى transfer.sh: {response.text}")
        raise Exception("فشل الرفع إلى transfer.sh")
        
    except Exception as e:
        logger.error(f"خطأ في الرفع إلى transfer.sh: {str(e)}")
        raise

def upload_to_gofile(file_path, filename=None):
    """رفع الملف إلى gofile.io"""
    try:
        logger.info("محاولة الرفع إلى gofile.io")
        
        # الحصول على خادم متاح
        server_response = requests.get('https://api.gofile.io/getServer', timeout=30)
        if server_response.status_code != 200:
            raise Exception("فشل في الحصول على خادم gofile")
        
        server_data = server_response.json()
        if not server_data.get('status') == 'ok':
            raise Exception("فشل في الحصول على خادم gofile")
        
        server = server_data['data']['server']
        
        if filename is None:
            filename = os.path.basename(file_path)
        
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f)}
            response = requests.post(
                f'https://{server}.gofile.io/uploadFile',
                files=files,
                timeout=300
            )
        
        if response.status_code == 200:
            json_response = response.json()
            if json_response.get('status') == 'ok':
                download_link = f"https://gofile.io/d/{json_response['data']['code']}"
                return download_link
        
        logger.error(f"فشل الرفع إلى gofile: {response.text}")
        raise Exception("فشل الرفع إلى gofile")
        
    except Exception as e:
        logger.error(f"خطأ في الرفع إلى gofile: {str(e)}")
        raise

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
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            if downloaded_size > 0:
                headers['Range'] = f'bytes={downloaded_size}-'
            
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

def upload_file_service(file_path, filename=None, service='fileio'):
    """رفع الملف باستخدام الخدمة المحددة"""
    services = {
        'fileio': upload_to_fileio,
        'anonfiles': upload_to_anonfiles,
        'transfersh': upload_to_transfersh,
        'gofile': upload_to_gofile
    }
    
    if service in services:
        return services[service](file_path, filename)
    else:
        # جرب جميع الخدمات بالترتيب
        for service_name, upload_func in services.items():
            try:
                logger.info(f"جرب خدمة: {service_name}")
                return upload_func(file_path, filename)
            except Exception as e:
                logger.warning(f"فشلت خدمة {service_name}: {str(e)}")
                continue
        
        raise Exception("فشل الرفع في جميع الخدمات المتاحة")

# --- دوال المعالجة (Async) ---
async def process_direct_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة الإرسال المباشر عبر تليجرام"""
    chat_id = update.effective_chat.id
    query = update.callback_query
    
    try:
        await query.answer()
        await query.edit_message_text("📤 تم اختيار الإرسال المباشر عبر تليجرام\n\n⏳ جاري التحميل...")
    except:
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
                    f"⚠️ يرجى إعادة المحاولة واختيار رفع الملف إلى خدمة تخزين",
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

async def process_cloud_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
    """معالجة رفع الملف إلى خدمة تخزين سحابية"""
    chat_id = update.effective_chat.id
    query = update.callback_query
    
    try:
        await query.answer()
        await query.edit_message_text("☁️ تم اختيار رفع الملف إلى خدمة تخزين\n\n⏳ جاري التحميل...")
    except:
        message = await context.bot.send_message(chat_id, "☁️ تم اختيار رفع الملف إلى خدمة تخزين\n\n⏳ جاري التحميل...")
        query = type('obj', (object,), {'message': message})()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, filename)
            
            loop = asyncio.get_running_loop()
            await query.edit_message_text("📥 جاري تحميل الملف من الرابط...")
            
            file_size_mb = await loop.run_in_executor(None, download_file, url, file_path)
            
            await query.edit_message_text("☁️ جاري رفع الملف إلى الخدمة السحابية...")
            
            # محاولة رفع الملف باستخدام أفضل خدمة متاحة
            try:
                download_link = await loop.run_in_executor(None, upload_file_service, file_path, filename, 'fileio')
                service_name = "file.io"
            except Exception as e:
                logger.warning(f"فشلت file.io، جرب خدمة أخرى: {str(e)}")
                try:
                    download_link = await loop.run_in_executor(None, upload_file_service, file_path, filename, 'gofile')
                    service_name = "gofile.io"
                except Exception as e2:
                    logger.warning(f"فشلت gofile، جرب خدمة أخرى: {str(e2)}")
                    try:
                        download_link = await loop.run_in_executor(None, upload_file_service, file_path, filename, 'anonfiles')
                        service_name = "anonfiles.com"
                    except Exception as e3:
                        logger.error(f"فشلت جميع الخدمات: {str(e3)}")
                        raise Exception("فشل الرفع في جميع الخدمات المتاحة. يرجى المحاولة لاحقاً.")
            
            if not download_link:
                raise Exception("فشل الحصول على رابط تحميل")

            # إرسال النتيجة
            message_text = (
                f"✅ **تم رفع الملف بنجاح!**\n\n"
                f"📄 الاسم: `{filename}`\n"
                f"📦 الحجم: `{file_size_mb:.2f} MB`\n"
                f"🌐 الخدمة: {service_name}\n"
                f"🔗 رابط التحميل المباشر:\n{download_link}\n\n"
                f"📌 *ملاحظة:*\n"
                f"• روابط file.io صالحة لمدة 14 يومًا\n"
                f"• روابط gofile.io صالحة لمدة 10 أيام\n"
                f"• يمكنك مشاركة الرابط مع الآخرين"
            )
            
            # إضافة زر لنسخ الرابط
            keyboard = [
                [InlineKeyboardButton("📋 نسخ الرابط", callback_data=f"copy:{download_link}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(message_text, parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error in cloud upload: {e}")
        error_msg = f"❌ حدث خطأ أثناء رفع الملف:\n\n{str(e)}\n\n⚠️ يرجى المحاولة مرة أخرى أو استخدام الإرسال المباشر إذا كان الملف صغيراً."
        try:
            await query.edit_message_text(error_msg)
        except:
            await context.bot.send_message(chat_id, error_msg)

async def handle_copy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة نسخ الرابط"""
    query = update.callback_query
    await query.answer()
    
    # استخراج الرابط من callback_data
    link = query.data.split(':', 1)[1]
    
    # إرسال رسالة منفصلة تحتوي على الرابط لسهولة النسخ
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"📋 **الرابط للنسخ:**\n\n`{link}`\n\nيمكنك نسخ الرابط من الأعلى ☝️",
        parse_mode='Markdown'
    )

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
    if not filename.endswith(('.mkv', '.mp4', '.avi', '.mov', '.webm', '.flv', '.wmv', '.m4v')):
        filename += ".mp4"

    url = context.user_data['url']
    
    # حفظ البيانات مؤقتاً
    context.user_data['filename'] = filename

    # عرض خيارات الإرسال للمستخدم
    keyboard = [
        [
            InlineKeyboardButton("📤 إرسال مباشر عبر تليجرام", callback_data="direct"),
            InlineKeyboardButton("☁️ رفع إلى خدمة تخزين", callback_data="cloud")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📄 **تم تحديد الملف:** `{filename}`\n\n"
        f"📊 **الحد الأقصى للإرسال المباشر:** {MAX_DIRECT_SIZE} MB\n\n"
        f"📍 **اختر طريقة الإرسال:**\n"
        f"• 📤 **إرسال مباشر:** أرسل الملف لك مباشرة عبر تليجرام (للملفات الصغيرة)\n"
        f"• ☁️ **رفع إلى خدمة تخزين:** تحصل على رابط تحميل مباشر (للملفات الكبيرة)\n\n"
        f"⚠️ *ملاحظة:* إذا اخترت الإرسال المباشر وكان الملف أكبر من {MAX_DIRECT_SIZE}MB، سيفشل الإرسال",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار المستخدم"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    
    # التحقق مما إذا كان هذا طلب نسخ رابط
    if query.data.startswith('copy:'):
        await handle_copy_link(update, context)
        return
    
    # استخراج البيانات من user_data
    url = context.user_data.get('url')
    filename = context.user_data.get('filename')
    
    if not url or not filename:
        await query.answer("⚠️ انتهت الجلسة، يرجى إعادة العملية من البداية", show_alert=True)
        return
    
    await query.answer()
    
    if query.data == "direct":
        await process_direct_send(update, context, url, filename)
    elif query.data == "cloud":
        await process_cloud_upload(update, context, url, filename)
    
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
        "• ☁️ **رفع إلى خدمة تخزين:** تحصل على رابط تحميل مباشر\n\n"
        f"⚠️ **حدود الإرسال المباشر:** {MAX_DIRECT_SIZE} MB\n\n"
        "🌐 **الخدمات المدعومة:** file.io, gofile.io, anonfiles.com\n\n"
        "🚀 ابدأ الآن بإرسال رابط الفيديو!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /help"""
    help_text = (
        f"📖 **مساعدة في استخدام البوت:**\n\n"
        f"**خطوات الاستخدام:**\n"
        f"1. أرسل رابط الفيديو (يجب أن يكون رابطاً مباشراً للتحميل)\n"
        f"2. أرسل اسم الحلقة أو الملف\n"
        f"3. اختر طريقة الإرسال:\n"
        f"   - 📤 **إرسال مباشر:** للملفات الصغيرة (أقل من {MAX_DIRECT_SIZE}MB)\n"
        f"   - ☁️ **رفع إلى خدمة تخزين:** للملفات الكبيرة (تحصل على رابط تحميل مباشر)\n\n"
        f"**الخدمات المتاحة:**\n"
        f"• file.io - صالح لمدة 14 يومًا\n"
        f"• gofile.io - صالح لمدة 10 أيام\n"
        f"• anonfiles.com - صالح لمدة غير محددة\n\n"
        f"**ملاحظات مهمة:**\n"
        f"• الروابط يجب أن تكون مباشرة للتحميل\n"
        f"• الإرسال المباشر قد يفشل للملفات الكبيرة\n"
        f"• يمكن مشاركة روابط التحميل مع الآخرين\n"
        f"• البوت يحاول عدة خدمات تلقائياً إذا فشلت إحداها\n\n"
        f"**مشاكل شائعة:**\n"
        f"• إذا فشل الإرسال المباشر: استخدم خيار الخدمة السحابية\n"
        f"• إذا فشل التحميل: تأكد من صحة الرابط\n"
        f"• إذا لم يظهر خيار الإرسال: أعد إرسال الرابط"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /cancel"""
    # تنظيف بيانات المستخدم
    context.user_data.clear()
    await update.message.reply_text("✅ تم إلغاء العملية الحالية. يمكنك البدء من جديد بإرسال رابط.")

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
    app_bot.add_handler(CommandHandler("cancel", cancel_command))
    
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
    
    # معالج Callback Queries (للأزرار)
    app_bot.add_handler(CallbackQueryHandler(handle_callback))

    # بدء البوت
    logger.info("Bot is running...")
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()