FROM python:3.11-slim

# تثبيت التبعيات النظامية
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# إنشاء دليل العمل
WORKDIR /app

# نسخ ملف المتطلبات وتثبيت التبعيات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ الكود المصدري
COPY . .

# تشغيل البوت
CMD ["python", "bot.py"]
