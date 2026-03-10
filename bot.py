import os
import json
import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import requests
import feedparser
from deep_translator import GoogleTranslator
from textblob import TextBlob
from dotenv import load_dotenv
from telegram.ext import Application, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
GNEWS_URL = f"https://gnews.io/api/v4/search?q=crypto&lang=en&token={GNEWS_API_KEY}"
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
MARKETAUX_URL = (
    "https://api.marketaux.com/v1/news/all"
    "?filter_entities=true&language=en&categories=cryptocurrency"
    f"&api_token={MARKETAUX_API_KEY}"
)
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
CACHE_FILE = "posted_cache.json"

ASSETS = [
    "bitcoin", "ethereum", "binancecoin", "solana", "chainlink", "polkadot", "cosmos",
    "avalanche-2", "near", "render-token", "aave", "uniswap", "ripple",
    "ethereum-name-service", "thorchain", "vechain", "cardano", "bitget-token",
    "curve-dao-token", "jupiter-exchange", "filecoin", "arbitrum"
]

MAX_POSTS_PER_RUN = 5
MAX_POSTS_PER_DAY = 20
BANNED_DOMAINS = ["biztoc.com", "pypi.org"]
IMPORTANT_KEYWORDS = ["hack", "listing", "etf", "regulation", "partnership", "lawsuit", "court"]
TOPIC_TAGS = {
    "bitcoin": "#Bitcoin", "btc": "#Bitcoin", "ethereum": "#Ethereum", "eth": "#Ethereum",
    "sec": "#SEC", "etf": "#ETF", "binance": "#Binance", "coinbase": "#Coinbase", "ftx": "#FTX",
    "bybit": "#Bybit", "blackrock": "#BlackRock", "hack": "#Hack", "exploit": "#Exploit",
    "scam": "#Scam", "fraud": "#Fraud", "defi": "#DeFi", "solana": "#Solana", "cardano": "#Cardano",
    "usdt": "#Tether", "tether": "#Tether", "ripple": "#XRP", "xrp": "#XRP", "kraken": "#Kraken",
    "regulation": "#Regulation", "lawsuit": "#Court", "court": "#Court", "ai": "#AI",
    "stablecoin": "#Stablecoin", "nft": "#NFT", "crypto": "#Crypto", "blockchain": "#Blockchain",
    "web3": "#Web3", "altcoin": "#Altcoins", "altcoins": "#Altcoins"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"hashes": set(), "urls": set(), "titles": set(), "date": "", "posts_today": 0}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "hashes": set(data.get("hashes", [])),
            "urls": set(data.get("urls", [])),
            "titles": set(data.get("titles", [])),
            "date": data.get("date", ""),
            "posts_today": data.get("posts_today", 0)
        }
    except Exception as e:
        logger.warning(f"❌ Cache error: {e}")
        return {"hashes": set(), "urls": set(), "titles": set(), "date": "", "posts_today": 0}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "hashes": list(cache["hashes"]),
            "urls": list(cache["urls"]),
            "titles": list(cache["titles"]),
            "date": cache.get("date", ""),
            "posts_today": cache.get("posts_today", 0),
        }, f, indent=2, ensure_ascii=False)

def sanitize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<.*?>|&[a-z]+;", "", text or "")).strip()

def generate_post_hash(title: str, body: str) -> str:
    return sha256(sanitize_text(title + body).encode("utf-8")).hexdigest()

def contextual_translate(title, body):
    try:
        result = GoogleTranslator(source="auto", target="uk").translate(f"Заголовок: {title}
Опис: {body}")
        parts = result.split("Опис:")
        ukr_title = parts[0].replace("Заголовок:", "").strip()
        ukr_body = parts[1].strip() if len(parts) > 1 else body
        return ukr_title, ukr_body
    except Exception as e:
        logger.warning(f"⚠️ Translation error: {e}")
        return title, body

def create_contextual_summary(text):
    text_l = text.lower()
    for k in IMPORTANT_KEYWORDS:
        if k in text_l:
            return {
                "hack": "🚨 Імовірно злом або втрата даних.",
                "etf": "📈 ETF - потужний інструмент для інституцій.",
                "lawsuit": "⚖️ Юридичні суперечки можуть змінити хід подій.",
                "court": "⚖️ Юридичні суперечки можуть змінити хід подій.",
                "listing": "📢 Новий лістинг підвищує популярність токена.",
                "partnership": "🤝 Партнерства відкривають нові горизонти.",
            }.get(k, "")
    return "📌 Подія з потенційним впливом на ринок."

def analyze_sentiment(text):
    polarity = TextBlob(text).sentiment.polarity
    return "🟢 Позитивна" if polarity > 0.2 else "🔴 Негативна" if polarity < -0.2 else "🟡 Нейтральна"

def extract_tags(text):
    text_l = text.lower()
    tags = {tag for kw, tag in TOPIC_TAGS.items() if kw in text_l}
    tags.add("#CryptoCourierUA")
    return " ".join(sorted(tags))

async def post_crypto_news(context: ContextTypes.DEFAULT_TYPE):
    logger.info("▶️ Fetching news...")
    cache = load_cache()
    combined = []
    today = datetime.now().strftime("%Y-%m-%d")
    if cache.get("date") != today: cache.update({"date": today, "posts_today": 0})

    try:
        r1 = requests.get("https://api.coinstats.app/public/v1/news?skip=0&limit=10&category=cryptocurrency", timeout=10)
        combined += [{"title": n.get("title", ""), "body": n.get("description", ""), "image": n.get("imgUrl"), "url": n.get("link")} for n in r1.json().get("news", [])]
    except Exception: pass

    try:
        r2 = requests.get(GNEWS_URL, timeout=10)
        combined += [{"title": g.get("title", ""), "body": g.get("description", ""), "image": g.get("image"), "url": g.get("url")} for g in r2.json().get("articles", [])]
    except Exception: pass

    posts_sent = 0
    for post in combined:
        if posts_sent >= MAX_POSTS_PER_RUN or cache["posts_today"] >= MAX_POSTS_PER_DAY: break
        title, body, url = sanitize_text(post["title"]), sanitize_text(post["body"]), post["url"] or ""
        if not title or not body or len(body.split()) < 5 or any(d in url for d in BANNED_DOMAINS): continue
        phash = generate_post_hash(title, body)
        if phash in cache["hashes"] or url in cache["urls"] or title in cache["titles"]: continue

        ut, ub = contextual_translate(title, body)
        logic, sent, tags = create_contextual_summary(title+body), analyze_sentiment(body), extract_tags(title+body)
        msg = f"🗳️ {ut}

{ub}

{logic}
{sent}

📊 {tags}
🔗 {url}"

        try:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="HTML")
            cache["hashes"].add(phash); cache["urls"].add(url); cache["titles"].add(title)
            cache["posts_today"] += 1; posts_sent += 1
            save_cache(cache)
        except Exception as e: logger.error(f"❌ Post error: {e}")

async def post_price_update(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = requests.get(f"{COINGECKO_PRICE_URL}?ids={','.join(ASSETS)}&vs_currencies=usd", timeout=10).json()
        lines = [f"{s.upper()}: ${data[s]['usd']:,.2f}" for s in ASSETS if s in data]
        msg = f"💹 Оновлення цін

{'
'.join(lines)}

#CryptoCourierUA"
        await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="HTML")
    except Exception: pass

async def main():
    app = Application.builder().token(TOKEN).build()
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    scheduler.add_job(post_crypto_news, trigger="interval", minutes=120, args=[app])
    scheduler.add_job(post_price_update, trigger="cron", hour="2,6,10,14,18,22", args=[app])
    scheduler.start()
    logger.info("🤖 Bot started")
    await app.initialize(); await app.start()
    try:
        while True: await asyncio.sleep(3600)
    finally:
        await app.stop(); scheduler.shutdown()

if __name__ == "__main__": asyncio.run(main())
