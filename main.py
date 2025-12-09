import os
import requests
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from flask import Flask
import threading
import time
import tempfile
import logging
import re
import base64

تكوين السجلات

logging.basicConfig(
format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
level=logging.INFO
)
logger = logging.getLogger(name)

الحصول على متغيرات البيئة

TOKEN = os.getenv("TOKEN")
MAX_DIRECT_SIZE = 47  # الحد الأقصى لإرسال الملفات مباشرة عبر التلجرام (MB)

تطبيق Flask لإبقاء الخادم نشطًا

app = Flask(name)

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

def upload_to_pixeldrain(file_path, filename=None):
"""رفع الملف إلى Pixeldrain باستخدام الطريقة الصحيحة"""
max_retries = 3
for attempt in range(max_retries):
try:
logger.info(f"محاولة الرفع إلى Pixeldrain (المحاولة {attempt+1})")

# إعداد رؤوس الطلب  
        headers = {}  
        api_key = os.getenv("PIXELDRAIN_API_KEY")  
        if api_key:  
            # التنسيق الصحيح: ":" + api_key  
            auth_str = f":{api_key}"  
            b64_auth = base64.b64encode(auth_str.encode()).decode()  
            headers["Authorization"] = f"Basic {b64_auth}"  
            logger.info(f"تم إعداد مصادقة API")  
          
        # إعداد ملف للرفع  
        with open(file_path, 'rb') as f:  
            files = {'file': (filename, f)} if filename else {'file': f}  
              
            # إرسال الطلب الصحيح باستخدام multipart/form-data  
            response = requests.post(  
                'https://pixeldrain.com/api/file',  
                files=files,  
                headers=headers,  
                timeout=300  # 5 دقائق مهلة  
            )  
          
        # تسجيل تفاصيل الاستجابة لفحص الأخطاء  
        logger.info(f"حالة الاستجابة: {response.status_code}")  
          
        response.raise_for_status()  
        json_response = response.json()  
          
        if json_response.get('success', False):  
            file_id = json_response.get('id')  
            if file_id:  
                return f"https://pixeldrain.com/api/file/{file_id}"  
            else:  
                raise Exception("فشل في الحصول على ID الملف من الاستجابة")  
        else:  
            error_msg = json_response.get('value', 'Unknown error')  
            raise Exception(f"فشل الرفع: {error_msg}")  
              
    except Exception as e:  
        logger.error(f"فشل الرفع إلى Pixeldrain: {str(e)}")  
        if attempt == max_retries - 1:  
            raise Exception(f"فشل الرفع بعد {max_retries} محاولات: {str(e)}")  
        time.sleep(10)  # انتظر 10 ثواني قبل إعادة المحاولة

def download_file(url, file_path):
"""تحميل الملف مع دعم الاستئناف وإعادة المحاولة"""
max_retries = 5
retry_delay = 10  # ثواني بين المحاولات
downloaded_size = 0

# استئناف التحميل من آخر نقطة  
if os.path.exists(file_path):  
    downloaded_size = os.path.getsize(file_path)  
  
for attempt in range(max_retries):  
    try:  
        headers = {  
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',  
            'Range': f'bytes={downloaded_size}-'  
        }  
          
        with requests.get(url, headers=headers, stream=True, timeout=60) as response:  
            response.raise_for_status()  
              
            # الحصول على الحجم الكلي  
            total_size = int(response.headers.get('Content-Length', 0)) + downloaded_size  
            size_mb = total_size / (1024 * 1024)  
              
            # متابعة التحميل  
            mode = 'ab' if downloaded_size > 0 else 'wb'  
            with open(file_path, mode) as f:  
                for chunk in response.iter_content(chunk_size=8192):  
                    if chunk:  
                        f.write(chunk)  
                        downloaded_size += len(chunk)  
              
            # التحقق من اكتمال التحميل  
            if downloaded_size == total_size:  
                logger.info(f"تم تحميل الملف بنجاح: {file_path}")  
                return size_mb  
            else:  
                raise Exception("التحميل غير مكتمل")  
      
    except Exception as e:  
        logger.error(f"فشل التحميل (المحاولة {attempt+1}): {str(e)}")  
        if attempt == max_retries - 1:  
            raise Exception(f"فشل التحميل بعد {max_retries} محاولات")  
        time.sleep(retry_delay)  
  
raise Exception("فشل تحميل الملف")

def sanitize_filename(name):
"""تنظيف اسم الملف وإزالة الأحرف غير المسموح بها"""
return re.sub(r'[^\w-_. ]', '', name).strip()

def get_file_size(url):
"""الحصول على حجم الملف باستخدام طريقة GET بدلاً من HEAD"""
try:
headers = {
'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
'Range': 'bytes=0-1'
}

response = requests.get(url, headers=headers, stream=True, timeout=15)  
    response.raise_for_status()  
      
    content_range = response.headers.get('Content-Range')  
    if content_range:  
        file_size = int(content_range.split('/')[1])  
    else:  
        file_size = int(response.headers.get('Content-Length', 0))  
      
    return file_size  
  
except Exception as e:  
    logger.error(f"فشل الحصول على حجم الملف: {str(e)}")  
    raise Exception(f"فشل الحصول على حجم الملف: {str(e)}")

async def process_large_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
"""معالجة الملفات الكبيرة (تحميل + رفع إلى Pixeldrain)"""
chat_id = update.message.chat_id
message = await context.bot.send_message(
chat_id=chat_id,
text="⏳ بدأت عملية تحميل الحلقة الكبيرة..."
)

try:  
    with tempfile.TemporaryDirectory() as temp_dir:  
        file_path = os.path.join(temp_dir, filename)  
          
        # تحميل الملف مع التحديثات  
        await context.bot.edit_message_text(  
            chat_id=chat_id,  
            message_id=message.message_id,  
            text="📥 جاري تحميل الحلقة من السيرفر (قد يستغرق عدة دقائق)..."  
        )  
        file_size_mb = download_file(url, file_path)  
          
        # رفع الملف إلى Pixeldrain  
        await context.bot.edit_message_text(  
            chat_id=chat_id,  
            message_id=message.message_id,  
            text="☁️ جاري رفع الحلقة إلى Pixeldrain (قد يستغرق عدة دقائق)..."  
        )  
        download_link = upload_to_pixeldrain(file_path, filename)  
          
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
        text=f"❌ حدث خطأ أثناء معالجة الحلقة الكبيرة:\n{str(e)}\n\n"  
             "يرجى المحاولة مرة أخرى لاحقًا"  
    )

async def process_small_file(update: Update, context: ContextTypes.DEFAULT_TYPE, url, filename):
"""معالجة الملفات الصغيرة (إرسال مباشر عبر التلجرام)"""
chat_id = update.message.chat_id
message = await context.bot.send_message(
chat_id=chat_id,
text="⏬ جاري تحميل وإرسال الحلقة مباشرةً..."
)

try:  
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
          
        # إعادة المحاولة عند فشل الإرسال  
        max_retries = 3  
        for attempt in range(max_retries):  
            try:  
                with open(file_path, 'rb') as file:  
                    await context.bot.send_document(  
                        chat_id=chat_id,  
                        document=InputFile(file, filename=filename),  
                        caption=f"📦 حجم الملف: {file_size_mb:.1f} ميجابايت\n📄 اسم الحلقة: {filename}"  
                    )  
                break  
            except Exception as e:  
                if attempt == max_retries - 1:  
                    raise  
                logger.warning(f"فشل الإرسال (المحاولة {attempt+1}): {str(e)}")  
                time.sleep(5)  
          
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
        text=f"❌ حدث خطأ أثناء معالجة الحلقة الصغيرة:\n{str(e)}\n\n"  
             "يرجى المحاولة مرة أخرى لاحقًا"  
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

if name == 'main':
main()