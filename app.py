import os
from flask import Flask
from threading import Thread
import asyncio
from bot import main as bot_main

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running"

@app.route('/health')
def health():
    return "OK", 200

def run_bot():
    asyncio.run(bot_main())

if __name__ == "__main__":
    t = Thread(target=run_bot, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
