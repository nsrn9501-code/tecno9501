FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# مجلد البيانات الدائم (HF Spaces)
RUN mkdir -p /data

# تفعيل وضع HF Spaces تلقائياً إذا كان التخزين الدائم متاحاً
ENV HF_SPACE=1

CMD ["python", "run.py"]
