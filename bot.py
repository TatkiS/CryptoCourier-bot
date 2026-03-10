import os
import json
import asyncio
import requests
from datetime import datetime
from hashlib import sha256
from deep_translator import GoogleTranslator
from telegram.ext import Application
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

def load_cache():
    try:
        with open("posted_cache.json", "r") as f: return json.load(f)
    except: return {"urls": [], "date": ""}

def save_cache(cache):
    with open("posted_cache.json", "w") as f: json.dump(cache, f)

async def post_news(context):
    cache = load_cache()
    if cache["date"] != datetime.now().strftime("%Y-%m-%d"): cache = {"urls": [], "date": datetime.now().strftime("%Y-%m-%d")}
    try:
        news = requests.get("https://api.coinstats.app/public/v1/news?skip=0&limit=5", timeout=10).json()["news"]
        for n in news:
            if n["link"] not in cache["urls"]:
                trans = GoogleTranslator(source="auto", target="uk").translate(n["title"] + "

" + n["description"])
                await context.bot.send_message(chat_id=CHANNEL_ID, text=trans + "

🔗 " + n["link"])
                cache["urls"].append(n["link"])
                save_cache(cache)
                break
    except: pass

async def main():
    app = Application.builder().token(TOKEN).build()
    sch = AsyncIOScheduler()
    sch.add_job(post_news, "interval", minutes=120, args=[app])
    sch.start()
    await app.initialize(); await app.start()
    while True: await asyncio.sleep(3600)

if __name__ == "__main__": asyncio.run(main())
