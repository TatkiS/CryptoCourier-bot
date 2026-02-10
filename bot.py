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
from aiohttp import web

# === 🌍 Завантаження змінних середовища ===
load_dotenv()

# === 🔧 Налаштування ===
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
GNEWS_URL = f"https://gnews.io/api/v4/search?q=crypto&lang=en&token={GNEWS_API_KEY}"
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
MARKETAUX_URL = f"https://api.marketaux.com/v1/news/all?filter_entities=true&language=en&categories=cryptocurrency&api_token={MARKETAUX_API_KEY}"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"

CACHE_FILE = "posted_cache.json"

ASSETS = ["bitcoin", "ethereum", "binancecoin", "solana", "chainlink", "polkadot", 
          "cosmos", "avalanche-2", "near", "render-token", "aave", "uniswap", 
          "ripple", "ethereum-name-service", "thorchain", "vechain", "cardano", 
          "bitget-token", "curve-dao-token", "jupiter-exchange", "filecoin", "arbitrum"]

MAX_POSTS_PER_RUN = 5
BANNED_DOMAINS = ["biztoc.com", "pypi.org"]
IMPORTANT_KEYWORDS = ["hack", "listing", "etf", "regulation", "partnership", "lawsuit", "court"]

TOPIC_TAGS = {
    "bitcoin": "#Bitcoin", "btc": "#Bitcoin",
    "ethereum": "#Ethereum", "eth": "#Ethereum",
    "sec": "#SEC", "etf": "#ETF",
    "binance": "#Binance", "coinbase": "#Coinbase",
    "ftx": "#FTX", "bybit": "#Bybit",
    "blackrock": "#BlackRock",
    "hack": "#Hack", "exploit": "#Exploit",
    "scam": "#Scam", "fraud": "#Fraud",
    "defi": "#DeFi", "solana": "#Solana",
    "cardano": "#Cardano",
    "usdt": "#Tether", "tether": "#Tether",
    "ripple": "#XRP", "xrp": "#XRP",
    "kraken": "#Kraken",
    "regulation": "#Regulation",
    "lawsuit": "#Court", "court": "#Court",
    "ai": "#AI", "stablecoin": "#Stablecoin",
    "nft": "#NFT", "crypto": "#Crypto",
    "blockchain": "#Blockchain", "web3": "#Web3",
    "altcoin": "#Altcoins", "altcoins": "#Altcoins"
}

logging.basicConfig(level=logging.INFO)

# === 🔄 Scheduler ===
scheduler = AsyncIOScheduler()

# === 📦 Кешування ===
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
        logging.warning(f"❌ Кеш не завантажено: {e}")
        return {"hashes": set(), "urls": set(), "titles": set(), "date": "", "posts_today": 0}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "hashes": list(cache["hashes"]),
            "urls": list(cache["urls"]),
            "titles": list(cache["titles"]),
            "date": cache.get("date", ""),
            "posts_today": cache.get("posts_today", 0)
        }, f, indent=2, ensure_ascii=False)

# === 📜 Інструменти ===
def sanitize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<.*?>|&[a-z]+;", "", text or "")).strip()

def generate_post_hash(title: str, body: str) -> str:
    return sha256(sanitize_text(title + body).encode("utf-8")).hexdigest()

def contextual_translate(title, body):
    try:
        result = GoogleTranslator(source='auto', target='uk').translate(f"Заголовок: {title}\nОпис: {body}")
        parts = result.split("Опис:")
        return parts[0].replace("Заголовок:", "").strip(), parts[1].strip() if len(parts) > 1 else body
    except:
        return title, body

def create_contextual_summary(text):
    text = text.lower()
    for k in IMPORTANT_KEYWORDS:
        if k in text:
            return {
                "hack": "🚨 Імовірно злом або втрата даних.",
                "etf": "📈 ETF - потужний інструмент для інституцій.",
                "lawsuit": "⚖️ Юридичні суперечки можуть змінити хід подій.",
                "court": "⚖️ Юридичні суперечки можуть змінити хід подій.",
                "listing": "📢 Новий лістинг підвищує популярність токена.",
                "partnership": "🤝 Партнерства відкривають нові горизонти."
            }.get(k, "")
    return "📌 Це подія, яка потенційно може вплинути на крипторинок найближчим часом."

def analyze_sentiment(text):
    polarity = TextBlob(text).sentiment.polarity
    return "🟢 Позитивна" if polarity > 0.2 else "🔴 Негативна" if polarity < -0.2 else "🟡 Нейтральна"

def extract_tags(text):
    return " ".join(sorted({tag for kw, tag in TOPIC_TAGS.items() if kw in text.lower()} | {"#CryptoCourierUA"}))

def is_valid_news(title, body):
    return bool(title and body and len(body.split()) > 5)

def is_image_accessible(url):
    try:
        r = requests.get(url, timeout=10)
        return r.status_code == 200 and "image" in r.headers.get("Content-Type", "")
    except:
        return False

# === 📡 Джерела новин ===
def fetch_marketaux():
    try:
        r = requests.get(MARKETAUX_URL, timeout=10)
        return r.json().get("data", [])
    except:
        return []

def fetch_gnews():
    try:
        r = requests.get(GNEWS_URL, timeout=10)
        return r.json().get("articles", [])
    except:
        return []

def fetch_coinstats():
    try:
        r = requests.get("https://api.coinstats.app/public/v1/news?skip=0&limit=10&category=cryptocurrency", timeout=10)
        return r.json().get("news", [])
    except:
        return []

def fetch_rss():
    feed = feedparser.parse("https://cointelegraph.com/rss")
    return feed.entries[:10]

# === 📰 Основна функція: публікація новин ===
async def post_crypto_news(context: ContextTypes.DEFAULT_TYPE):
    cache = load_cache()
    combined = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    if cache.get("date") != today:
        cache.update({"date": today, "posts_today": 0})
    
    combined += [{
        "title": n.get("title", ""),
        "body": n.get("description", "") or n.get("content", ""),
        "image": n.get("imgUrl"),
        "url": n.get("link")
    } for n in fetch_coinstats()]
    
    combined += [{
        "title": g.get("title", ""),
        "body": g.get("description", "") or g.get("content", ""),
        "image": g.get("image"),
        "url": g.get("url")
    } for g in fetch_gnews()]
    
    combined += [{
        "title": m.get("title", ""),
        "body": m.get("description", "") or m.get("snippet", ""),
        "image": m.get("image_url"),
        "url": m.get("url")
    } for m in fetch_marketaux()]
    
    combined += [{
        "title": r.get("title", ""),
        "body": r.get("summary", ""),
        "image": "",
        "url": r.get("link")
    } for r in fetch_rss()]
    
    logging.info(f"📊 Комбіновано новин: {len(combined)}")
    
    posts_sent = 0
    for post in combined:
        if posts_sent >= MAX_POSTS_PER_RUN or cache["posts_today"] >= 20:
            break
        
        title = sanitize_text(post["title"])
        body = sanitize_text(post["body"])
        url = post["url"]
        
        if not is_valid_news(title, body) or any(d in url for d in BANNED_DOMAINS):
            continue
        
        post_hash = generate_post_hash(title, body)
        if post_hash in cache["hashes"] or url in cache["urls"] or title in cache["titles"]:
            continue
        
        ukr_title, ukr_body = contextual_translate(title, body)
        logic = create_contextual_summary(title + " " + body)
        sentiment = analyze_sentiment(body)
        tags = extract_tags(title + " " + body)
        
        msg = f"""🗳️ {ukr_title}

{ukr_body}

{logic}
{sentiment}

📊 {tags}
🔗 Читати повністю: {url}"""
        
        try:
            if post["image"] and is_image_accessible(post["image"]):
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=post["image"], caption=msg, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="HTML")
            
            cache["hashes"].add(post_hash)
            cache["urls"].add(url)
            cache["titles"].add(title)
            cache["posts_today"] += 1
            posts_sent += 1
            save_cache(cache)
            
            await asyncio.sleep(2)  # Пауза між постами
            
        except Exception as e:
            logging.error(f"❌ Не вдалося надіслати пост: {e}")
    
    if posts_sent == 0:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text="📭 Сьогодні новин, вартих уваги, не знайдено. Слідкуй за оновленнями!",
            parse_mode="HTML"
        )

# === 💰 Ціни ===
async def post_price_update(context: ContextTypes.DEFAULT_TYPE):
    try:
        url = f"{COINGECKO_PRICE_URL}?ids={','.join(ASSETS)}&vs_currencies=usd"
        data = requests.get(url, timeout=10).json()
        now = datetime.now(timezone(timedelta(hours=3))).strftime('%Y-%m-%d %H:%M')
        
        prices = "\n".join(f"{sym.upper()}: ${data[sym]['usd']:,.2f}" for sym in data if 'usd' in data[sym])
        
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"💹 Оновлення цін ({now})\n\n📊 Поточні ціни:\n{prices}\n\n#CryptoCourierUA #Ціни",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"❌ Помилка отримання цін: {e}")

# === 📰 Wrapper-функції для scheduler ===
async def scheduled_post_news(app):
    """Wrapper для виклику post_crypto_news через scheduler"""
    class FakeContext:
        def __init__(self, bot):
            self.bot = bot
    
    await post_crypto_news(FakeContext(app.bot))

async def scheduled_price_update(app):
    """Wrapper для виклику post_price_update через scheduler"""
    class FakeContext:
        def __init__(self, bot):
            self.bot = bot
    
    await post_price_update(FakeContext(app.bot))

# === 🌐 HTTP Server для Health Check ===
async def health_check(request):
    """Health check endpoint для Render"""
    return web.Response(text="OK", status=200)

async def start_http_server():
    """Запуск HTTP сервера для health checks"""
    app_web = web.Application()
    app_web.router.add_get('/', health_check)
    app_web.router.add_get('/health', health_check)
    
    port = int(os.getenv('PORT', 10000))
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"✅ HTTP сервер запущено на порту {port}")
    return runner

# === 🚀 Головний цикл ===
async def main():
    app = Application.builder().token(TOKEN).build()
    
    # Додаємо задачі в scheduler
    scheduler.add_job(scheduled_post_news, trigger='interval', minutes=60, args=[app])
    scheduler.add_job(scheduled_price_update, trigger='cron', hour='2,6,10,14,18,22', args=[app])
    scheduler.start()
    
    # Запуск HTTP сервера
    http_runner = await start_http_server()
    logging.info("🤖 CryptoCourierUA запущено")
    
    await app.initialize()
    await app.start()
    
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logging.info("🛑 Зупинка бота...")
        await http_runner.cleanup()
        await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
