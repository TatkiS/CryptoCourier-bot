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

# === Завантаження змінних середовища ===
load_dotenv()

# === Налаштування ===
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
        logger.warning(f"❌ Кеш не завантажено: {e}")
        return {"hashes": set(), "urls": set(), "titles": set(), "date": "", "posts_today": 0}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "hashes": list(cache["hashes"]),
                "urls": list(cache["urls"]),
                "titles": list(cache["titles"]),
                "date": cache.get("date", ""),
                "posts_today": cache.get("posts_today", 0),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

def sanitize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<.*?>|&[a-z]+;", "", text or "")).strip()

def generate_post_hash(title: str, body: str) -> str:
    return sha256(sanitize_text(title + body).encode("utf-8")).hexdigest()

def contextual_translate(title, body):
    try:
        result = GoogleTranslator(source="auto", target="uk").translate(
            f"Заголовок: {title}
Опис: {body}"
        )
        parts = result.split("Опис:")
        ukr_title = parts[0].replace("Заголовок:", "").strip()
        ukr_body = parts[1].strip() if len(parts) > 1 else body
        return ukr_title, ukr_body
    except Exception as e:
        logger.warning(f"⚠️ Помилка перекладу: {e}")
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
    return "📌 Це подія, яка потенційно може вплинути на крипторинок найближчим часом."

def analyze_sentiment(text):
    polarity = TextBlob(text).sentiment.polarity
    if polarity > 0.2:
        return "🟢 Позитивна"
    elif polarity < -0.2:
        return "🔴 Негативна"
    else:
        return "🟡 Нейтральна"

def extract_tags(text):
    text_l = text.lower()
    tags = {tag for kw, tag in TOPIC_TAGS.items() if kw in text_l}
    tags.add("#CryptoCourierUA")
    return " ".join(sorted(tags))

def is_valid_news(title, body):
    return bool(title and body and len(body.split()) > 5)

def is_image_accessible(url):
    if not url:
        return False
    try:
        r = requests.get(url, timeout=10)
        return r.status_code == 200 and "image" in r.headers.get("Content-Type", "")
    except Exception:
        return False

def fetch_marketaux():
    if not MARKETAUX_API_KEY:
        return []
    try:
        r = requests.get(MARKETAUX_URL, timeout=10)
        return r.json().get("data", [])
    except Exception as e:
        logger.error(f"❌ Marketaux помилка: {e}")
        return []

def fetch_gnews():
    if not GNEWS_API_KEY:
        return []
    try:
        r = requests.get(GNEWS_URL, timeout=10)
        return r.json().get("articles", [])
    except Exception as e:
        logger.error(f"❌ GNews помилка: {e}")
        return []

def fetch_coinstats():
    try:
        r = requests.get(
            "https://api.coinstats.app/public/v1/news?skip=0&limit=10&category=cryptocurrency",
            timeout=10,
        )
        return r.json().get("news", [])
    except Exception as e:
        logger.error(f"❌ Coinstats помилка: {e}")
        return []

def fetch_rss():
    try:
        feed = feedparser.parse("https://cointelegraph.com/rss")
        return feed.entries[:10]
    except Exception as e:
        logger.error(f"❌ RSS помилка: {e}")
        return []

async def post_crypto_news(context: ContextTypes.DEFAULT_TYPE):
    logger.info("▶️ Запуск постингу новин")
    cache = load_cache()
    combined = []

    today = datetime.now().strftime("%Y-%m-%d")
    if cache.get("date") != today:
        cache.update({"date": today, "posts_today": 0})

    combined += [
        {
            "title": n.get("title", ""),
            "body": n.get("description", "") or n.get("content", ""),
            "image": n.get("imgUrl"),
            "url": n.get("link"),
        }
        for n in fetch_coinstats()
    ]

    combined += [
        {
            "title": g.get("title", ""),
            "body": g.get("description", "") or g.get("content", ""),
            "image": g.get("image"),
            "url": g.get("url"),
        }
        for g in fetch_gnews()
    ]

    combined += [
        {
            "title": m.get("title", ""),
            "body": m.get("description", "") or m.get("snippet", ""),
            "image": m.get("image_url"),
            "url": m.get("url"),
        }
        for m in fetch_marketaux()
    ]

    combined += [
        {
            "title": r.get("title", ""),
            "body": r.get("summary", ""),
            "image": "",
            "url": r.get("link"),
        }
        for r in fetch_rss()
    ]

    logger.info(f"📊 Комбіновано новин: {len(combined)}")

    posts_sent = 0
    for post in combined:
        if posts_sent >= MAX_POSTS_PER_RUN or cache["posts_today"] >= MAX_POSTS_PER_DAY:
            break

        title = sanitize_text(post["title"])
        body = sanitize_text(post["body"])
        url = post["url"] or ""

        if not is_valid_news(title, body) or any(d in url for d in BANNED_DOMAINS):
            continue

        post_hash = generate_post_hash(title, body)
        if (
            post_hash in cache["hashes"]
            or url in cache["urls"]
            or title in cache["titles"]
        ):
            continue

        ukr_title, ukr_body = contextual_translate(title, body)
        logic = create_contextual_summary(title + " " + body)
        sentiment = analyze_sentiment(body)
        tags = extract_tags(title + " " + body)

        msg = (
            f"🗳️ {ukr_title}

"
            f"{ukr_body}

"
            f"{logic}
"
            f"{sentiment}

"
            f"📊 {tags}
"
            f"🔗 Читати повністю: {url}"
        )

        try:
            if is_image_accessible(post["image"]):
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=post["image"],
                    caption=msg,
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=msg,
                    parse_mode="HTML",
                )

            cache["hashes"].add(post_hash)
            cache["urls"].add(url)
            cache["titles"].add(title)
            cache["posts_today"] += 1
            posts_sent += 1
            save_cache(cache)
        except Exception as e:
            logger.error(f"❌ Не вдалося надіслати пост: {e}")

async def post_price_update(context: ContextTypes.DEFAULT_TYPE):
    logger.info("▶️ Запуск оновлення цін")
    try:
        url = f"{COINGECKO_PRICE_URL}?ids={','.join(ASSETS)}&vs_currencies=usd"
        data = requests.get(url, timeout=10).json()
        now = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
        lines = []
        for sym in ASSETS:
            if sym in data and "usd" in data[sym]:
                price = data[sym]["usd"]
                lines.append(f"{sym.upper()}: ${price:,.2f}")
        prices = "
".join(lines)

        msg = (
            f"💹 Оновлення цін ({now})

"
            f"📊 Поточні ціни:
{prices}

"
            f"#CryptoCourierUA #Ціни"
        )
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=msg,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"❌ Помилка отримання цін: {e}")

async def main():
    if not TOKEN or not CHANNEL_ID:
        logger.error("❌ BOT_TOKEN або CHANNEL_ID відсутні!")
        return

    app = Application.builder().token(TOKEN).build()
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")

    scheduler.add_job(post_crypto_news, trigger="interval", minutes=120, args=[app])
    scheduler.add_job(
        post_price_update,
        trigger="cron",
        hour="2,6,10,14,18,22",
        args=[app],
    )

    scheduler.start()
    logger.info("🤖 CryptoCourierUA запущено")

    await app.initialize()
    await app.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Зупинка бота...")
    finally:
        await app.stop()
        scheduler.shutdown(wait=False)

if __name__ == "__main__":
    asyncio.run(main())
