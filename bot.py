import os
import json
import logging
from datetime import datetime
import pytz
import feedparser
from telegram import Bot
from telegram.error import TelegramError
import asyncio

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфігурація
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
RSS_FEEDS = [
    'https://www.coindesk.com/arc/outboundfeeds/rss/',
    'https://cointelegraph.com/rss',
    'https://decrypt.co/feed'
]
CACHE_FILE = 'posted_cache.json'
CHECK_INTERVAL = 300  # 5 хвилин

# Ініціалізація бота
bot = Bot(token=BOT_TOKEN)

def load_cache():
    """Завантажує кеш опублікованих новин"""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Помилка завантаження кешу: {e}")
    return {'posted_ids': []}

def save_cache(cache):
    """Зберігає кеш опублікованих новин"""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Помилка збереження кешу: {e}")

def format_news(entry):
    """Форматує новину для публікації"""
    title = entry.get('title', 'Без заголовка')
    link = entry.get('link', '')
    published = entry.get('published', '')
    
    # Форматування дати
    try:
        pub_date = datetime.strptime(published, '%a, %d %b %Y %H:%M:%S %z')
        kyiv_tz = pytz.timezone('Europe/Kiev')
        pub_date_kyiv = pub_date.astimezone(kyiv_tz)
        date_str = pub_date_kyiv.strftime('%d.%m.%Y %H:%M')
    except:
        date_str = published
    
    message = f"🔔 <b>{title}</b>\n\n"
    message += f"📅 {date_str}\n"
    message += f"🔗 <a href='{link}'>Читати повністю</a>\n\n"
    message += "#криптоновини #CryptoCourier"
    
    return message

async def check_and_post_news():
    """Перевіряє RSS і публікує нові новини"""
    cache = load_cache()
    posted_ids = set(cache.get('posted_ids', []))
    
    for feed_url in RSS_FEEDS:
        try:
            logger.info(f"Перевірка RSS: {feed_url}")
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:5]:  # Беремо тільки 5 останніх
                entry_id = entry.get('id', entry.get('link'))
                
                if entry_id not in posted_ids:
                    try:
                        message = format_news(entry)
                        await bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=message,
                            parse_mode='HTML',
                            disable_web_page_preview=False
                        )
                        
                        posted_ids.add(entry_id)
                        logger.info(f"Опубліковано: {entry.get('title')}")
                        
                        # Затримка між постами
                        await asyncio.sleep(2)
                        
                    except TelegramError as e:
                        logger.error(f"Помилка публікації: {e}")
                        
        except Exception as e:
            logger.error(f"Помилка обробки RSS {feed_url}: {e}")
    
    # Зберігаємо тільки останні 1000 ID
    cache['posted_ids'] = list(posted_ids)[-1000:]
    save_cache(cache)

async def main():
    """Основний цикл бота"""
    logger.info("Бот запущено")
    
    while True:
        try:
            await check_and_post_news()
            logger.info(f"Очікування {CHECK_INTERVAL} секунд...")
            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Помилка в основному циклі: {e}")
            await asyncio.sleep(60)

if __name__ == '__main__':
    asyncio.run(main())
