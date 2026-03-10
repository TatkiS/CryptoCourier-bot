import os
import json
import asyncio
import requests
from datetime import datetime
from deep_translator import GoogleTranslator
from telegram.ext import Application
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from flask import Flask
from threading import Thread

flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Bot is running"

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

def load_cache():
    try:
        with open("posted_cache.json", "r") as f: return json.load(f)
    except: return {"urls": [], "date": ""}

def save_cache(cache):
    with open("posted_cache.json", "w") as f: json.dump(cache, f)

async def post_news(context):
    print(f"[{datetime.now()}] Starting news check...")
    cache = load_cache()
    current_date = datetime.now().strftime("%Y-%m-%d")
    if cache.get("date") != current_date:
        cache = {"urls": [], "date": current_date}
    try:
        url = "https://api.coinstats.app/public/v1/news?skip=0&limit=10"
        response = requests.get(url, timeout=15)
        news_data = response.json().get("news", [])
        posted = False
        for item in news_data:
            link = item.get("link")
            if link and link not in cache["urls"]:
                title = item.get("title", "No Title")
                description = item.get("description", "")
                text_to_translate = f"📌 {title}\n\n{description}"
                translated = GoogleTranslator(source='auto', target='uk').translate(text_to_translate)
                final_message = f"{translated}\n\n🔗 {link}"
                await context.bot.send_message(chat_id=CHANNEL_ID, text=final_message)
                cache["urls"].append(link)
                save_cache(cache)
                print(f"[{datetime.now()}] Posted: {title}")
                posted = True
                break
        if not posted:
            print(f"[{datetime.now()}] No new news to post.")
    except Exception as e:
        print(f"[{datetime.now()}] Error: {e}")

async def main():
    print("Bot starting...")
    keep_alive()
    application = Application.builder().token(TOKEN).build()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(post_news, 'interval', hours=2, args=[application], next_run_time=datetime.now())
    scheduler.start()
    await application.initialize()
    await application.start()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
