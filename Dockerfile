FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# yt-dlp обновляется на старте: платформы регулярно ломают старые версии
CMD ["sh", "-c", "pip install --no-cache-dir -q -U yt-dlp; python3 main.py"]
