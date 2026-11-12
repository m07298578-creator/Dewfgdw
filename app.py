import os
import threading
from flask import Flask
import asyncio
from bot import main as bot_main  # Импортируем твою функцию запуска бота

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running"

@app.route('/health')
def health():
    return "OK", 200

def run_bot():
    # Запускаем твой бот в отдельном потоке
    asyncio.run(bot_main())

if __name__ == "__main__":
    # Запускаем бота в фоне
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Запускаем веб-сервер Flask
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)