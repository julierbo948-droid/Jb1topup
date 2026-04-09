import os
import datetime
import asyncio
from collections import defaultdict
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', 6437656033))
GOOGLE_EMAIL = os.getenv('GOOGLE_EMAIL')
GOOGLE_PASS = os.getenv('GOOGLE_PASS')

if not BOT_TOKEN:
    print("❌ Error: BOT_TOKEN is missing in the .env file.")
    exit()

MMT = datetime.timezone(datetime.timedelta(hours=6, minutes=30))

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Global States
IS_MAINTENANCE = False
GLOBAL_SCAMMERS = set()
user_locks = defaultdict(asyncio.Lock)
api_semaphore = asyncio.Semaphore(10)
auth_lock = asyncio.Lock()

WEBSHARE_PROXIES = [
    "http://qduuujrj:bf1ttoecf2d5@31.59.20.176:6754",
    "http://qduuujrj:bf1ttoecf2d5@23.95.150.145:6114",
    "http://qduuujrj:bf1ttoecf2d5@198.23.239.134:6540",
    "http://qduuujrj:bf1ttoecf2d5@45.38.107.97:6014",
    "http://qduuujrj:bf1ttoecf2d5@107.172.163.27:6543",
    "http://qduuujrj:bf1ttoecf2d5@198.105.121.200:6462",
    "http://qduuujrj:bf1ttoecf2d5@216.10.27.159:6837",
    "http://qduuujrj:bf1ttoecf2d5@142.111.67.146:5611",
    "http://qduuujrj:bf1ttoecf2d5@191.96.254.138:6185",
    "http://qduuujrj:bf1ttoecf2d5@31.58.9.4:6077"
]
