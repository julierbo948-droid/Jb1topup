import asyncio
import datetime
import concurrent.futures
import re

from aiogram import BaseMiddleware, types
from aiogram.enums import ParseMode

import database as db
import config
from config import bot, dp, MMT, OWNER_ID
import easy_bby
from helpers import notify_owner

import handlers

class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data: dict):
        if config.IS_MAINTENANCE:
            if event.from_user.id != OWNER_ID:
                await event.reply("⚠️ ပြုပြင်ဆောင်ရွက်နေပါသဖြင့် Topup ဘော့အား ခနရပ်ထားပါသည်။")
                return 
        return await handler(event, data)

class ScamAlertMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data: dict):
        if event.text:
            text_lower = event.text.lower()
            
            if text_lower.startswith(".scam ") or text_lower.startswith(".unscam ") or text_lower.startswith("/scam") or text_lower.startswith("/unscam"):
                return await handler(event, data)
                
            for scam_id in config.GLOBAL_SCAMMERS:
                pattern = rf"\b{scam_id}\b"
                if re.search(pattern, event.text):
                    await event.reply(
                        "Scamer game id , Scamer Alert!",
                        parse_mode=ParseMode.HTML
                    )
                    break 
        return await handler(event, data)

async def keep_cookie_alive():
    while True:
        try:
            await asyncio.sleep(3 * 60) 
            scraper = await easy_bby.get_main_scraper()
            headers = {'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest', 'Origin': 'https://www.smile.one'}
            response = await scraper.get('https://www.smile.one/customer/order', headers=headers)
            if "login" not in str(response.url).lower() and response.status_code == 200:
                pass 
            else:
                print(f"[{datetime.datetime.now(MMT).strftime('%I:%M %p')}] ⚠️ Main Cookie expired unexpectedly.")
                await notify_owner("⚠️ <b>System Warning:</b> Cookie သက်တမ်းကုန်သွားသည်ကို တွေ့ရှိရပါသည်။ Auto-Login စတင်နေပါသည်...")
                success = await easy_bby.auto_login_and_get_cookie()
                if not success: await notify_owner("❌ <b>Critical:</b> Auto-Login မအောင်မြင်ပါ။ သင့်အနေဖြင့် `/setcookie` ဖြင့် Cookie အသစ် လာရောက်ထည့်သွင်းပေးရန် လိုအပ်ပါသည်။")
        except Exception: pass

async def schedule_daily_cookie_renewal():
    while True:
        now = datetime.datetime.now(MMT)
        target_time = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if now >= target_time: target_time += datetime.timedelta(days=1)
        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        success = await easy_bby.auto_login_and_get_cookie()
        if success:
            try: await bot.send_message(OWNER_ID, "✅ <b>System:</b> Proactive cookie renewal successful. Ready for the day!", parse_mode=ParseMode.HTML)
            except Exception: pass

async def daily_reconciliation_task():
    while True:
        now = datetime.datetime.now(MMT)
        target_time = now.replace(hour=23, minute=50, second=0, microsecond=0)
        if now >= target_time: target_time += datetime.timedelta(days=1)
        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        try:
            db_summary = await db.get_today_orders_summary()
            db_total_spent = db_summary['total_spent']
            db_order_count = db_summary['total_orders']
            
            scraper = await easy_bby.get_main_scraper()
            headers = {'X-Requested-With': 'XMLHttpRequest', 'Origin': 'https://www.smile.one'}
            balances = await easy_bby.get_smile_balance(scraper, headers)
            
            # စာလုံးအမဲနဲ့ အကြီး (Heading ပုံစံ) ဖြစ်အောင် <b> tag သုံးထားပါတယ်
            report = (
                "<b>📊 DAILY RECONCILIATION REPORT 📊</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "<b>1. BOT SYSTEM (V-WALLET) RECORDS:</b>\n"
                f"🔹 Total Orders Today: <code>{db_order_count}</code>\n"
                f"🔹 Total Spent Today: <b>${db_total_spent:,.2f}</b>\n\n"
                "<b>2. OFFICIAL SMILE.ONE BALANCES:</b>\n"
                f"🇧🇷 BR: <code>${balances.get('br_balance', 0.0):,.2f}</code>\n"
                f"🇵🇭 PH: <code>${balances.get('ph_balance', 0.0):,.2f}</code>\n\n"
                "<i>(Please verify if the balances align with your expected expenses.)</i>"
            )
            # notify_owner ထဲမှာ parse_mode="HTML" ပါဖို့ လိုအပ်ပါတယ်
            await notify_owner(report ,  parse_mode="HTML")
        except Exception as e: print(f"Reconciliation Error: {e}")

async def send_broadcast_greeting(text: str):
    users = await db.get_all_resellers()
    for u in users:
        try:
            tg_id = int(u['tg_id'])
            await bot.send_message(chat_id=tg_id, text=text, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.1) 
        except Exception: pass

async def schedule_morning_greeting():
    while True:
        now = datetime.datetime.now(MMT)
        target = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= target: target += datetime.timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        await send_broadcast_greeting("🌅 <b>ဒီနေ့ဟာ မနေ့ကထက် ပိုကောင်းတဲ့နေ့ ဖြစ်လာဖို့အတွက် အစပျိုးပေးလိုက်ပါ။ အခက်အခဲတွေကို ကျော်ဖြတ်ဖို့ ကြိုးစားနေတဲ့ သင့်အတွက် ဒီနေ့မှာ ပျော်ရွှင်မှုနဲ့ အောင်မြင်မှုတွေ ပိုင်ဆိုင်နိုင်ပါစေ။ သာယာသောမင်္ဂလာနံနက်ခင်းလေးဖြစ်ပါစေခဗျာ...။</b>")

async def schedule_night_greeting():
    while True:
        now = datetime.datetime.now(MMT)
        target = now.replace(hour=23, minute=30, second=0, microsecond=0)
        if now >= target: target += datetime.timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        await send_broadcast_greeting("🌙 <b>ဒီနေ့မှာ အောင်မြင်ခဲ့တာပဲဖြစ်ဖြစ်၊ အခက်အခဲလေးတွေ ရှိခဲ့တာပဲဖြစ်ဖြစ် အားလုံးပြီးဆုံးသွားပါပြီ။ ကိုယ့်ကိုယ်ကိုယ် ဂရုစိုက်ပြီး အနားယူလိုက်ပါ။ မနက်ဖြန်ဟာ သင့်အတွက် ပိုကောင်းတဲ့အခွင့်အရေးတွေနဲ့အတူ ပိုမိုတောက်ပတဲ့ နေ့သစ်တစ်ခုနဲ့ တွေ့ဆုံနိုင်ပါစေ။ Good night ပါ။</b>")

async def main():
    print("Starting Heartbeat & Auto-login tasks...")
    print("နှလုံးသားမပါရင် ဘယ်အရာမှတရားမဝင်")
    
    loop = asyncio.get_running_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=50))
    
    try:
        scammer_list = await db.get_all_scammers()
        config.GLOBAL_SCAMMERS = set(scammer_list)
        print(f"Loaded {len(config.GLOBAL_SCAMMERS)} Scammer IDs.")
    except Exception as e:
        print(f"Error loading scammers: {e}")

    dp.message.middleware(MaintenanceMiddleware())
    dp.message.middleware(ScamAlertMiddleware())


    
    asyncio.create_task(keep_cookie_alive())
    asyncio.create_task(schedule_daily_cookie_renewal())
    asyncio.create_task(daily_reconciliation_task())
    asyncio.create_task(schedule_morning_greeting())
    asyncio.create_task(schedule_night_greeting())
    
    await db.setup_indexes()
    await db.init_owner(OWNER_ID)
    print("Bot is successfully running on Aiogram 3 Framework... 🎉")
    
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
