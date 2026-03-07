#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv venv
fi

source venv/bin/activate

pip install -q -r requirements.txt 2>/dev/null

echo "Запуск бота..."
python3 main.py
