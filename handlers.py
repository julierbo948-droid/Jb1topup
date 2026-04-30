import io
import re
import time
import random
import asyncio
import html
import json

from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from easy_bby import get_random_proxy
from aiogram import F, types
from aiogram.filters import Command, or_f
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from curl_cffi.requests import AsyncSession

import database as db
import config
from easy_bby import get_main_scraper
from config import dp, bot, OWNER_ID, MMT, user_locks, api_semaphore
from packages import DOUBLE_DIAMOND_PACKAGES, BR_PACKAGES, PH_PACKAGES, MCC_PACKAGES, PH_MCC_PACKAGES
from helpers import is_authorized, notify_owner, generate_list
import easy_bby


async def execute_buy_process(message, lines, regex_pattern, currency, packages_dict, process_func, title_prefix, is_mcc=False):
    tg_id = str(message.from_user.id)
    telegram_user = message.from_user.username
    
    if telegram_user:
        user_link = f'<a href="https://t.me/{telegram_user}">@{telegram_user}</a>'
    else:
        user_link = f'<a href="tg://user?id={tg_id}">{tg_id}</a>'
        
    v_bal_key = 'br_balance' if currency == 'BR' else 'ph_balance'
    
    async with user_locks[tg_id]: 
        parsed_orders = []
        
        for line in lines:
            line = line.strip()
            if not line: continue 
            
            match = re.search(regex_pattern, line)
            if not match:
                await message.reply(f"Invalid format: `{line}`\nCheck /help for correct format.")
                continue
                
            game_id = match.group(1)
            zone_id = match.group(2)
            raw_items_str = match.group(3).lower()
            
            requested_packages = raw_items_str.split()
            packages_to_buy = [] 
            not_found_pkgs = []
            
            for pkg in requested_packages:
                active_packages = None
                if isinstance(packages_dict, list):
                    for p_dict in packages_dict:
                        if pkg in p_dict: 
                            active_packages = p_dict
                            break
                else:
                    if pkg in packages_dict: 
                        active_packages = packages_dict
                        
                if active_packages: 
                    pkg_items = []
                    for item_dict in active_packages[pkg]:
                        new_item = item_dict.copy()
                        new_item['pkg_name'] = pkg.upper() 
                        pkg_items.append(new_item)
                    packages_to_buy.append({
                        'pkg_name': pkg.upper(),
                        'items': pkg_items
                    })
                else: 
                    not_found_pkgs.append(pkg)
                    
            if not_found_pkgs:
                await message.reply(f"❌ Package(s) not found for ID {game_id}: {', '.join(not_found_pkgs)}")
                continue
            if not packages_to_buy: 
                continue
                
            line_price = sum(item['price'] for p in packages_to_buy for item in p['items'])
            parsed_orders.append({
                'game_id': game_id, 
                'zone_id': zone_id, 
                'raw_items_str': raw_items_str, 
                'packages_to_buy': packages_to_buy, 
                'line_price': line_price
            })
            
        if not parsed_orders: 
            return

        user_wallet = await db.get_reseller(tg_id)
        user_v_bal = user_wallet.get(v_bal_key, 0.0) if user_wallet else 0.0
            
        # start_time ရဲ့ ရှေ့မှာ Space (၈) ခု ရှိရပါမယ်
        start_time = time.time()

        # loading_msg ရဲ့ ရှေ့မှာလည်း Space (၈) ခု ရှိရပါမယ်
        loading_msg = await message.reply(
            "<tg-emoji emoji-id='6186016335294636592'>❤️</tg-emoji>",
            parse_mode=ParseMode.HTML
        )

        # current_v_bal ရဲ့ ရှေ့မှာလည်း အပေါ်ကစာကြောင်းတွေနဲ့ တစ်တန်းတည်း (Space ၈ ခု) ဖြစ်ရပါမယ်
        current_v_bal = [user_v_bal]
        async def process_order_line(order):
            game_id = order['game_id']
            zone_id = order.get('order_zone', order['zone_id'])
            raw_items_str = order['raw_items_str']
            packages_to_buy = order['packages_to_buy']
            
            overall_success_count = 0
            overall_fail_count = 0
            total_spent = 0.0
            
            ig_name = "Unknown"
            package_results = [] 

            async with api_semaphore:
                prev_context = None
                last_success_order = ""
                
                for pkg_data in packages_to_buy:
                    pkg_name = pkg_data['pkg_name']
                    items = pkg_data['items']
                    
                    pkg_success_count = 0
                    pkg_fail_count = 0
                    pkg_spent = 0.0
                    pkg_order_ids = ""
                    pkg_error = ""
                    
                    pkg_total_price = sum(item['price'] for item in items)
                    
                    if current_v_bal[0] < pkg_total_price:
                        pkg_fail_count = len(items)
                        pkg_error = "Insufficient balance for the full package"
                        overall_fail_count += 1
                        package_results.append({
                            'pkg_name': pkg_name,
                            'status': 'fail',
                            'spent': 0.0,
                            'order_ids': "",
                            'error_msg': pkg_error,
                            'ig_name': ig_name
                        })
                        continue
                    
                    for item in items:
                        if current_v_bal[0] < item['price']:
                            pkg_fail_count += 1
                            pkg_error = "Insufficient balance"
                            break

                        current_v_bal[0] -= item['price']

                        skip_check = False 
                        res = {}
                        
                        max_retries = 3
                        for attempt in range(max_retries):
                            res = await process_func(
                                game_id, zone_id, item['pid'], currency, 
                                prev_context=prev_context, skip_role_check=skip_check, 
                                known_ig_name=ig_name, last_success_order_id=last_success_order
                            )
                            
                            error_text_check = str(res.get('message', '')).lower()
                            
                            
                            if res.get('status') == 'success' or "insufficient" in error_text_check or "invalid" in error_text_check or "not found" in error_text_check or "limit" in error_text_check or "exceed" in error_text_check or "máximo" in error_text_check:
                                break
                                
                            
                            if attempt < max_retries - 1:
                                if "erro no servidor" in error_text_check or "server error" in error_text_check or "cloudflare" in error_text_check or "query failed" in error_text_check:

                                    await asyncio.sleep(5.0)
                                else:
                                    
                                    await asyncio.sleep(2.0)
                                
                        fetched_name = res.get('ig_name') or res.get('username') or res.get('role_name') or res.get('nickname')
                        if fetched_name and str(fetched_name).strip() not in ["", "Unknown", "None"]:
                            ig_name = str(fetched_name).strip()

                        if res.get('status') == 'success':
                            pkg_success_count += 1
                            pkg_spent += item['price']
                            pkg_order_ids += f"{res.get('order_id', '')}\n"
                            prev_context = {'csrf_token': res.get('csrf_token')}
                            last_success_order = res.get('order_id', '')
                        else:
                            current_v_bal[0] += item['price']
                            pkg_fail_count += 1
                            pkg_error = res.get('message', 'Unknown Error')
                            break 
                            
                    if pkg_success_count > 0:
                        overall_success_count += 1
                        total_spent += pkg_spent
                        
                        display_name = pkg_name
                        if len(items) > 1 and pkg_success_count < len(items):
                            if pkg_name.upper().startswith("WP"):
                                display_name = f"WP{pkg_success_count}"
                            else:
                                display_name = f"{pkg_name} ({pkg_success_count}/{len(items)} Success)"
                                
                        package_results.append({
                            'pkg_name': display_name,
                            'status': 'success',
                            'spent': pkg_spent,
                            'order_ids': pkg_order_ids.strip(),
                            'error_msg': "",
                            'ig_name': ig_name
                        })
                        
                    if pkg_fail_count > 0:
                        overall_fail_count += 1
                        
                        display_name = pkg_name
                        if len(items) > 1 and pkg_fail_count < len(items):
                            if pkg_name.upper().startswith("WP"):
                                display_name = f"WP{len(items) - pkg_success_count}"
                            else:
                                display_name = f"{pkg_name} ({len(items) - pkg_success_count} Failed)"
                                
                        package_results.append({
                            'pkg_name': display_name,
                            'status': 'fail',
                            'spent': 0.0,
                            'order_ids': "",
                            'error_msg': pkg_error,
                            'ig_name': ig_name
                        })
                        
            return {
                'game_id': game_id, 
                'zone_id': zone_id, 
                'raw_items_str': raw_items_str, 
                'success_count': overall_success_count, 
                'fail_count': overall_fail_count, 
                'total_spent': total_spent, 
                'ig_name': ig_name,
                'package_results': package_results 
            }

        line_tasks = [process_order_line(order) for order in parsed_orders]
        line_results = await asyncio.gather(*line_tasks)
        time_taken_seconds = int(time.time() - start_time)
        await loading_msg.delete() 

        if not line_results: return

        now = datetime.now(MMT) 
        date_str = now.strftime("%m/%d/%Y, %I:%M:%S %p")

        f
        or res in line_results:
            tg_id = message.from_user.id
            user_name = message.from_user.full_name

            current_wallet = await db.get_reseller(tg_id)
            initial_bal_for_receipt = current_wallet.get(v_bal_key, 0.0) if current_wallet else 0.0
            
            if res['total_spent'] > 0:
                if currency == 'BR': await db.update_balance(tg_id, br_amount=-res['total_spent'])
                else: await db.update_balance(tg_id, ph_amount=-res['total_spent'])
                
            new_wallet = await db.get_reseller(tg_id)
            new_v_bal = new_wallet.get(v_bal_key, 0.0) if new_wallet else 0.0
            
            header_title = f"{title_prefix} {res['game_id']} ({res['zone_id']}) {res['raw_items_str'].upper()} ({currency})"
            
            report = f"<blockquote><pre>{header_title}\n"
            report += f"===== TRANSACTION REPORT =====\n"

            for pr in res['package_results']:
                safe_ig_name = html.escape(str(pr['ig_name']))

                if pr['status'] == 'success':
                    report += f"━━━━━━━━━━━━━━━━━━━━━\n"
                    report += f"GAME ID      : {res['game_id']} {res['zone_id']}\n"
                    report += f"IG NAME      : {safe_ig_name}\n"
                    report += f"ITEM         : {pr['pkg_name']} 💎| ✅\n"
                    report += f"SERIAL       :\n{pr['order_ids']}\n"
                    report += f"SPENT        : {pr['spent']:.2f} 🪙\n\n"
                    
                    final_order_ids = pr['order_ids'].replace('\n', ', ')
                    await db.save_order(
                        tg_id=tg_id, game_id=res['game_id'], zone_id=res['zone_id'], item_name=pr['pkg_name'], 
                        price=pr['spent'], order_id=final_order_ids, status="success"
                    )
                else:
                    error_text = str(pr['error_msg']).lower()
                    if "insufficient" in error_text or "saldo" in error_text: 
                        display_err = "Insufficient balance"
                    elif "invalid" in error_text or "not found" in error_text:
                        display_err = "Invalid Account"
                    elif "erro no servidor" in error_text or "server error" in error_text:
                        display_err = "Game Server Error (Please try again later)"
                    elif "query failed" in error_text:
                        display_err = "Smileone website api error try again."
                    elif "limit" in error_text or "exceed" in error_text or "máximo" in error_text or "limite" in error_text:
                        display_err = "Weekly Pass Limit Exceeded"
                    elif "zone" in error_text or "region" in error_text or "country" in error_text or "indonesia" in error_text or "support recharge" in error_text or "Singapore" in error_text or "Russia" in error_text or "the Philippines" in error_text:
                        display_err = "Ban Server"
                    else: 
                        display_err = pr['error_msg'].replace('❌', '').strip()
                        if not display_err: display_err = "Purchase Failed"
                        
                        if "wp" in pr['pkg_name'].lower():
                            if "unable" in error_text or "fail" in error_text or "error" in error_text:
                                display_err = "Weekly Pass Limit Exceeded"

                    report += f"━━━━━━━━━━━━━━━━━━━━━\n"
                    report += f"GAME ID      : {res['game_id']} {res['zone_id']}\n"
                    report += f"IG NAME      : {safe_ig_name}\n"
                    report += f"ITEM         : {pr['pkg_name']} 💎| ❌\n"
                    report += f"ERROR        : {display_err}\n\n"

            report += f"━━━━━━━━━━━━━━━━━━━━━\n"
            report += f"DATE         : {date_str}\n"
            report += f"===== ACCOUNT INFO =====\n"
            report += f"INITIAL      : ${initial_bal_for_receipt:,.2f}\n"
            report += f"FINAL        : ${new_v_bal:,.2f}\n\n"
            report += f"SUCCESS {res['success_count']} / FAIL {res['fail_count']}</pre></blockquote>"
            


            # (၁) ရလဒ်အပေါ် မူတည်ပြီး Button Style သတ်မှတ်ခြင်း
            if res['fail_count'] > 0:
                btn_style = "danger" 
                btn_text = f"| {user_name}"
                btn_icon = "6194857525473451865"
            else:
                btn_style = "success" 
                btn_text = f"| {user_name}"
                btn_icon = "6190228864988355594"

            # (၂) Keyboard တည်ဆောက်ခြင်း
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=btn_text,
                        url=f"tg://user?id={tg_id}",
                        style=btn_style ,
                        icon_custom_emoji_id=btn_icon
                    )
                ]
            ])


            try:
                await message.reply(
                    report, 
                    parse_mode="HTML", 
                    reply_markup=keyboard
                )
            except Exception as e:
                print(f"Final Report Error: {e}")
                await message.reply(f"❌ Report Formatting Error!")



#@dp.message(or_f(Command("add"), F.text.regexp(r"(?i)^\.add(?:$|\s+)")))
#async def add_reseller(message: types.Message):
#    if message.from_user.id != OWNER_ID: return await message.reply("You are not the Owner.")
#    parts = message.text.split()
#    if len(parts) < 2: return await message.reply("`/add <user_id>`")
#    target_id = parts[1].strip()
#    if not target_id.isdigit(): return await message.reply("Please enter the User ID in numbers only.")
#    if await db.add_reseller(target_id, f"User_{target_id}"):
#        await message.reply(f"✅ Reseller ID `{target_id}` has been approved.")
#    else:
#        await message.reply(f"Reseller ID `{target_id}` is already in the list.")

async def check_admin_validity(user_id: int):
    if user_id == OWNER_ID: return True
    
    user = await db.resellers_col.find_one({"tg_id": str(user_id)})
    if not user or not user.get("is_admin"):
        return False

    last_date = user.get("last_topup_date")
    if last_date and datetime.now() > last_date + timedelta(days=30):
        await db.resellers_col.update_one({"tg_id": str(user_id)}, {"$set": {"is_admin": False}})
        return False
    
    return True

async def re_add_admin_handler(message: types.Message):
    if message.from_user.id != OWNER_ID: return
    
    try:
        parts = message.text.split()
        if len(parts) < 2: return await message.reply("💡 Usage: <code>.readd [user_id]</code>")
        
        target_id = parts[1].strip()
        user = await db.resellers_col.find_one({"tg_id": target_id})
        
        if not user:
            return await message.reply("❌ User မရှိပါ။ အသစ်ဆိုလျှင် .add ကို သုံးပါ။")

        old_br = user.get("br_balance", 0)
        old_ph = user.get("ph_balance", 0)

        await db.resellers_col.update_one(
            {"tg_id": target_id},
            {"$set": {
                "is_admin": True,
                "last_topup_date": datetime.now(),
                "br_balance": 0.0,
                "ph_balance": 0.0
            }}
        )

        report = (
            f"👑 <b>Admin Re-Activation Report</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Admin ID:</b> <code>{target_id}</code>\n"
            f"💰 <b>မူလ လက်ကျန် Coins စာရင်း:</b>\n"
            f"🇧🇷 <b>Brazil:</b> {old_br} Coins\n"
            f"🇵🇭 <b>Philippines:</b> {old_ph} Coins\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<i>(ဤစာရင်းကို Owner သာ မြင်ရပါသည်။)</i>"
        )
        await message.reply(f"✅ Admin {target_id} ကို ပြန်ခန့်အပ်ပြီး သက်တမ်းတိုးပေးလိုက်ပါပြီ။")
        await message.bot.send_message(OWNER_ID, report, parse_mode="HTML")

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

async def remove_reseller(message: types.Message):
    if message.from_user.id != OWNER_ID: 
        return await message.reply("You are not the Owner.")
    
    parts = message.text.split()
    if len(parts) < 2: return await message.reply("Usage: `/remove <user_id>`")
    
    target_id = parts[1].strip()
    if target_id == str(OWNER_ID): return await message.reply("The Owner cannot be removed.")
    
    # Status ကိုပဲ ပိတ်မယ်
    success = await db.resellers_col.update_one(
        {"tg_id": target_id}, 
        {"$set": {"is_admin": False}}
    )

    if success.modified_count > 0:
        await message.reply(f"✅ Reseller ID `{target_id}` ကို ရိုးရိုး User အဖြစ် ပြောင်းလဲလိုက်ပါပြီ။")
    else:
        await message.reply("ID မရှိပါ သို့မဟုတ် သူသည် Admin မဟုတ်ပါ။")

@dp.message(or_f(Command("users"), F.text.regexp(r"(?i)^\.users$")))
async def list_resellers(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("You are not the Owner.")
    resellers_list = await db.get_all_resellers()
    user_list = []
    for r in resellers_list:
        role = "owner" if r["tg_id"] == str(OWNER_ID) else "users"
        user_list.append(f"🟢 ID: `{r['tg_id']}` ({role})\n   BR: ${r.get('br_balance', 0.0)} | PH: ${r.get('ph_balance', 0.0)}")
    final_text = "\n\n".join(user_list) if user_list else "No users found."
    await message.reply(f"🟢 <b>Approved users List (V-Wallet):</b>\n\n{final_text}",parse_mode="HTML")

@dp.message(Command("setcookie"))
async def set_cookie_command(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("❌ Only the Owner can set the Cookie.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.reply("⚠️ **Usage format:**\n`/setcookie <Long_Main_Cookie>`")
    await db.update_main_cookie(parts[1].strip())
    
    easy_bby.GLOBAL_SCRAPER = None
    easy_bby.GLOBAL_CSRF = {'mlbb_br': None, 'mlbb_ph': None, 'mcc_br': None, 'mcc_ph': None}
    await message.reply("✅ **Main Cookie has been successfully updated securely.**")

@dp.message(F.text.contains("PHPSESSID") & F.text.contains("cf_clearance"))
async def handle_smart_cookie_update(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("❌ You are not authorized.")
    text = message.text
    target_keys = ["PHPSESSID", "cf_clearance", "__cf_bm", "_did", "_csrf"]
    extracted_cookies = {}
    try:
        for key in target_keys:
            pattern = rf"['\"]?{key}['\"]?\s*[:=]\s*['\"]?([^'\",;\s}}]+)['\"]?"
            match = re.search(pattern, text)
            if match:
                extracted_cookies[key] = match.group(1)
        if "PHPSESSID" not in extracted_cookies or "cf_clearance" not in extracted_cookies:
            return await message.reply("❌ <b>Error:</b> `PHPSESSID` နှင့် `cf_clearance` ကို ရှာမတွေ့ပါ။ Format မှန်ကန်ကြောင်း စစ်ဆေးပါ။", parse_mode=ParseMode.HTML)
        formatted_cookie_str = "; ".join([f"{k}={v}" for k, v in extracted_cookies.items()])
        await db.update_main_cookie(formatted_cookie_str)
        
        easy_bby.GLOBAL_SCRAPER = None
        easy_bby.GLOBAL_CSRF = {'mlbb_br': None, 'mlbb_ph': None, 'mcc_br': None, 'mcc_ph': None}
        
        success_msg = "✅ <b>Cookies Successfully Extracted & Saved!</b>\n\n📦 <b>Extracted Data:</b>\n"
        for k, v in extracted_cookies.items():
            display_v = f"{v[:15]}...{v[-15:]}" if len(v) > 35 else v
            success_msg += f"🔸 <code>{k}</code> : {display_v}\n"
        success_msg += f"\n🍪 <b>Formatted Final String:</b>\n<code>{formatted_cookie_str}</code>"
        await message.reply(success_msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply(f"❌ <b>Parsing Error:</b> {str(e)}", parse_mode=ParseMode.HTML)

@dp.message(or_f(Command("addbal"), F.text.regexp(r"(?i)^\.addbal(?:$|\s+)")))
async def add_balance_command(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("❌ You are not authorized.")
    parts = message.text.strip().split()
    if len(parts) < 3: return await message.reply("⚠️ <b>Usage format:</b>\n`.addbal <User_ID> <Amount> [BR/PH]`")
    target_id = parts[1]
    try: amount = float(parts[2])
    except ValueError: return await message.reply("❌ Invalid amount.")
    currency = "BR"
    if len(parts) > 3:
        currency = parts[3].upper()
        if currency not in ['BR', 'PH']: return await message.reply("❌ Invalid currency.")
    target_wallet = await db.get_reseller(target_id)
    if not target_wallet: return await message.reply(f"❌ User ID `{target_id}` not found.")
    if currency == 'BR': await db.update_balance(target_id, br_amount=amount)
    else: await db.update_balance(target_id, ph_amount=amount)
    updated_wallet = await db.get_reseller(target_id)
    new_br = updated_wallet.get('br_balance', 0.0)
    new_ph = updated_wallet.get('ph_balance', 0.0)
    await message.reply(f"✅ <b>Balance Added Successfully!</b>\n\n👤 <b>User ID:</b> `{target_id}`\n💰 <b>Added:</b> `+{amount:,.2f} {currency}`\n\n📊 <b>Current Balance:</b>\n🇧🇷 BR: `${new_br:,.2f}`\n🇵🇭 PH: `${new_ph:,.2f}`")

@dp.message(or_f(Command("deduct"), F.text.regexp(r"(?i)^\.deduct(?:$|\s+)")))
async def deduct_balance_command(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("❌ You are not authorized.")
    parts = message.text.strip().split()
    if len(parts) < 3: return await message.reply("⚠️ **Usage format:**\n`.deduct <User_ID> <Amount> [BR/PH]`")
    target_id = parts[1]
    try: amount = abs(float(parts[2]))
    except ValueError: return await message.reply("❌ Invalid amount.")
    currency = "BR"
    if len(parts) > 3:
        currency = parts[3].upper()
        if currency not in ['BR', 'PH']: return await message.reply("❌ Invalid currency.")
    target_wallet = await db.get_reseller(target_id)
    if not target_wallet: return await message.reply(f"❌ User ID `{target_id}` not found.")
    if currency == 'BR': await db.update_balance(target_id, br_amount=-amount)
    else: await db.update_balance(target_id, ph_amount=-amount)
    updated_wallet = await db.get_reseller(target_id)
    new_br = updated_wallet.get('br_balance', 0.0)
    new_ph = updated_wallet.get('ph_balance', 0.0)
    await message.reply(f"✅ <b>Balance Deducted Successfully!</b>\n\n👤 **User ID:** `{target_id}`\n💸 <b>Deducted:</b> `-{amount:,.2f} {currency}`\n\n📊 <b>Current Balance:</b>\n🇧🇷 BR: `${new_br:,.2f}`\n🇵🇭 PH: `${new_ph:,.2f}`")

@dp.message(F.text.regexp(r"(?i)^\.topup\s+([a-zA-Z0-9]+)"))
async def handle_topup(message: types.Message):
    if not await is_authorized(message.from_user.id): return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    match = re.search(r"(?i)^\.topup\s+([a-zA-Z0-9]+)", message.text.strip())
    if not match: return await message.reply("Usage format - `.topup <Code>`")
    activation_code = match.group(1).strip()
    tg_id = str(message.from_user.id)
    user_id_int = message.from_user.id 
    loading_msg = await message.reply(f"Checking Code `{activation_code}`...")
    
    async with user_locks[tg_id]:
        scraper = await easy_bby.get_main_scraper()
        from bs4 import BeautifulSoup
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Accept': 'text/html'}
        
        async def try_redeem(api_type):
            if api_type == 'PH':
                page_url = 'https://www.smile.one/ph/customer/activationcode'
                check_url = 'https://www.smile.one/ph/smilecard/pay/checkcard'
                pay_url = 'https://www.smile.one/ph/smilecard/pay/payajax'
                base_origin = 'https://www.smile.one'
                base_referer = 'https://www.smile.one/ph/'
                balance_check_url = 'https://www.smile.one/ph/customer/order'
            else:
                page_url = 'https://www.smile.one/customer/activationcode'
                check_url = 'https://www.smile.one/smilecard/pay/checkcard'
                pay_url = 'https://www.smile.one/smilecard/pay/payajax'
                base_origin = 'https://www.smile.one'
                base_referer = 'https://www.smile.one/'
                balance_check_url = 'https://www.smile.one/customer/order'

            req_headers = headers.copy()
            req_headers['Referer'] = base_referer

            try:
                res = await scraper.get(page_url, headers=req_headers)
                if "login" in str(res.url).lower() or res.status_code in [403, 503]: return "expired", None

                soup = BeautifulSoup(res.text, 'html.parser')
                csrf_token = soup.find('meta', {'name': 'csrf-token'})
                csrf_token = csrf_token.get('content') if csrf_token else (soup.find('input', {'name': '_csrf'}).get('value') if soup.find('input', {'name': '_csrf'}) else None)
                if not csrf_token: return "expired", None 

                ajax_headers = req_headers.copy()
                ajax_headers.update({'X-Requested-With': 'XMLHttpRequest', 'Origin': base_origin, 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'})

                check_res_raw = await scraper.post(check_url, data={'_csrf': csrf_token, 'pin': activation_code}, headers=ajax_headers)
                check_res = check_res_raw.json()
                code_status = str(check_res.get('code', check_res.get('status', '')))
                
                card_amount = 0.0
                try:
                    if 'data' in check_res and isinstance(check_res['data'], dict):
                        val = check_res['data'].get('amount', check_res['data'].get('money', 0))
                        if val: card_amount = float(val)
                except: pass

                if code_status in ['200', '201', '0', '1'] or 'success' in str(check_res.get('msg', '')).lower():
                    old_bal = await easy_bby.get_smile_balance(scraper, headers, balance_check_url)
                    pay_res_raw = await scraper.post(pay_url, data={'_csrf': csrf_token, 'sec': activation_code}, headers=ajax_headers)
                    pay_res = pay_res_raw.json()
                    pay_status = str(pay_res.get('code', pay_res.get('status', '')))
                    
                    if pay_status in ['200', '0', '1'] or 'success' in str(pay_res.get('msg', '')).lower():
                        await asyncio.sleep(5) 
                        anti_cache_url = f"{balance_check_url}?_t={int(time.time())}"
                        new_bal = await easy_bby.get_smile_balance(scraper, headers, anti_cache_url)
                        bal_key = 'br_balance' if api_type == 'BR' else 'ph_balance'
                        added = round(new_bal[bal_key] - old_bal[bal_key], 2)
                        if added <= 0 and card_amount > 0: added = card_amount
                        return "success", added
                    else: return "fail", "Payment failed."
                else: return "invalid", "Invalid Code"
            except Exception as e: return "error", str(e)

        status, result = await try_redeem('BR')
        active_region = 'BR'
        if status in ['invalid', 'fail']: 
            status, result = await try_redeem('PH')
            active_region = 'PH'

        if status == "expired":
            await loading_msg.edit_text("⚠️ <b>Cookies Expired!</b>\n\nAuto-login စတင်နေပါသည်... ခဏစောင့်ပြီး ပြန်လည်ကြိုးစားပါ။", parse_mode=ParseMode.HTML)
            await notify_owner("⚠️ <b>Top-up Alert:</b> Code ဖြည့်သွင်းနေစဉ် Cookie သက်တမ်းကုန်သွားပါသည်။ Auto-login စတင်နေပါသည်...")
            success = await easy_bby.auto_login_and_get_cookie()
            if not success: await notify_owner("❌ <b>Critical:</b> Auto-Login မအောင်မြင်ပါ။ `/setcookie` ဖြင့် အသစ်ထည့်ပေးပါ။")
        elif status == "error": await loading_msg.edit_text(f"❌ Error: {result}")
        elif status in ['invalid', 'fail']: await loading_msg.edit_text("Cʜᴇᴄᴋ Fᴀɪʟᴇᴅ❌\n(Code is invalid or might have been used)")
        elif status == "success":
            added_amount = result
            if added_amount <= 0:
                await loading_msg.edit_text(f"sᴍɪʟᴇ ᴏɴᴇ ʀᴇᴅᴇᴇᴍ ᴄᴏᴅᴇ sᴜᴄᴄᴇss ✅\n(Cannot retrieve exact amount due to System Delay.)")
            else:
                if user_id_int == OWNER_ID: fee_percent = 0.0
                else:
                    if added_amount >= 10000: fee_percent = 0.1
                    elif added_amount >= 5000: fee_percent = 0.15
                    elif added_amount >= 1000: fee_percent = 0.2
                    elif added_amount >= 1120: fee_percent = 0.2    
                    elif added_amount >= 300: fee_percent = 0.3 
                    else: fee_percent = 0.0

                fee_amount = round(added_amount * (fee_percent / 100), 2)
                net_added = round(added_amount - fee_amount, 2)
        
                user_wallet = await db.get_reseller(tg_id)
                if active_region == 'BR':
                    assets = user_wallet.get('br_balance', 0.0) if user_wallet else 0.0
                    await db.update_balance(tg_id, br_amount=net_added)
                else:
                    assets = user_wallet.get('ph_balance', 0.0) if user_wallet else 0.0
                    await db.update_balance(tg_id, ph_amount=net_added)

                total_assets = assets + net_added
                fmt_amount = int(added_amount) if added_amount % 1 == 0 else added_amount

                msg = (f"✅ <b>Code Top-Up Successful</b>\n\n<code>Code   : {activation_code} ({active_region})\nAmount : {fmt_amount:,}\nFee    : -{fee_amount:.1f} ({fee_percent}%)\nAdded  : +{net_added:,.1f} 🪙\nAssets : {assets:,.1f} 🪙\nTotal  : {total_assets:,.1f} 🪙</code>")
                await loading_msg.edit_text(msg, parse_mode=ParseMode.HTML)

@dp.message(or_f(Command("balance"), F.text.regexp(r"(?i)^\.bal(?:$|\s+)")))
async def check_balance_command(message: types.Message):
    if not await is_authorized(message.from_user.id): return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    tg_id = str(message.from_user.id)
    user_wallet = await db.get_reseller(tg_id)
    if not user_wallet: return await message.reply("Yᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ ɪɴғᴏʀᴍᴀᴛɪᴏɴ ᴄᴀɴɴᴏᴛ ʙᴇ ғᴏᴜɴᴅ.")
    
    ICON_EMOJI = "6179070080391848842" 
    BR_EMOJI = "5228878788867142213"   
    PH_EMOJI = "5231361434583049965"   

    report = (f"<blockquote><tg-emoji emoji-id='{ICON_EMOJI}'>💳</tg-emoji> <b>𝗬𝗢𝗨𝗥 𝗪𝗔𝗟𝗟𝗘𝗧 𝗕𝗔𝗟𝗔𝗡𝗖𝗘</b>\n\n<tg-emoji emoji-id='{BR_EMOJI}'>🇧🇷</tg-emoji> 𝗕𝗥 𝗕𝗔𝗟𝗔𝗡𝗖𝗘 : ${user_wallet.get('br_balance', 0.0):,.2f}\n<tg-emoji emoji-id='{PH_EMOJI}'>🇵🇭</tg-emoji> 𝗣𝗛 𝗕𝗔𝗟𝗔𝗡𝗖𝗘 : ${user_wallet.get('ph_balance', 0.0):,.2f}</blockquote>")
    
    if message.from_user.id == OWNER_ID:
        loading_msg = await message.reply("Fetching real balance from the official account...")
        scraper = await easy_bby.get_main_scraper()
        headers = {'X-Requested-With': 'XMLHttpRequest', 'Origin': 'https://www.smile.one'}
        try:
            balances = await easy_bby.get_smile_balance(scraper, headers, 'https://www.smile.one/customer/order')
            report += (f"\n\n<blockquote><tg-emoji emoji-id='{ICON_EMOJI}'>💳</tg-emoji> <b>𝗢𝗙𝗙𝗜𝗖𝗜𝗔𝗟 𝗔𝗖𝗖𝗢𝗨𝗡𝗧 𝗕𝗔𝗟𝗔𝗡𝗖𝗘</b>\n\n<tg-emoji emoji-id='{BR_EMOJI}'>🇧🇷</tg-emoji> 𝗕𝗥 𝗕𝗔𝗟𝗔𝗡𝗖𝗘 : ${balances.get('br_balance', 0.00):,.2f}\n<tg-emoji emoji-id='{PH_EMOJI}'>🇵🇭</tg-emoji> 𝗣𝗛 𝗕𝗔𝗟𝗔𝗡𝗖𝗘 : ${balances.get('ph_balance', 0.00):,.2f}</blockquote>")
            await loading_msg.edit_text(report, parse_mode=ParseMode.HTML)
        except Exception as e:
            try: await loading_msg.edit_text(report, parse_mode=ParseMode.HTML)
            except: pass
    else:
        try: await message.reply(report, parse_mode=ParseMode.HTML)
        except: pass

@dp.message(or_f(Command("history"), F.text.regexp(r"(?i)^\.his$")))
async def send_order_history(message: types.Message):
    if not await is_authorized(message.from_user.id): return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    tg_id = str(message.from_user.id)
    user_name = message.from_user.username or message.from_user.first_name
    history_data = await db.get_user_history(tg_id, limit=200)
    if not history_data: return await message.reply("📜 **No Order History Found.**")
    response_text = f"==== Order History for @{user_name} ====\n\n"
    for order in history_data:
        response_text += (f"🆔 Game ID: {order['game_id']}\n🌏 Zone ID: {order['zone_id']}\n💎 Pack: {order['item_name']}\n🆔 Order ID: {order['order_id']}\n📅 Date: {order['date_str']}\n💲 Rate: ${order['price']:,.2f}\n📊 Status: {order['status']}\n────────────────\n")
    file_bytes = response_text.encode('utf-8')
    document = BufferedInputFile(file_bytes, filename=f"History_{tg_id}.txt")
    await message.answer_document(document=document, caption=f"📜 Order History\n👤 User: @{user_name}\n📊 Records: {len(history_data)}")

@dp.message(or_f(Command("clean"), F.text.regexp(r"(?i)^\.clean$")))
async def clean_order_history(message: types.Message):
    if not await is_authorized(message.from_user.id): return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    tg_id = str(message.from_user.id)
    deleted_count = await db.clear_user_history(tg_id)
    if deleted_count > 0: await message.reply(f"🗑️ History Cleaned Successfully.\nDeleted {deleted_count} order records from your history.")
    else: await message.reply("📜 No Order History Found to Clean.")

@dp.message(F.text.regexp(r"(?i)^(?:msc|mlb|br|b)\s+\d+"))
async def handle_br_mlbb(message: types.Message):
    if not await is_authorized(message.from_user.id): 
        return await message.reply(f"ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.❌")
    match = re.search(r"(?i)^(?:msc|mlb|br|b)\s*(\d+)", message.text.strip())
    game_id = match.group(1) if match else None    

   # if game_id and str(game_id) in config.GLOBAL_SCAMMERS:
    #    alert_text = (
     #       f"<code>{message.text}</code>\n\n"
      #      f"🚨 <b>Scammer Alert!</b>\n"
       #     f"ဒီ Game ID (<code>{game_id}</code>) သည် Scammer စာရင်းထဲတွင် ပါဝင်နေပါသဖြင့် ဝယ်ယူခွင့်ကို ပိတ်ပင်ထားပါသည်။ ❌"
        #)
        #return await message.reply(alert_text, parse_mode=ParseMode.HTML)
    try:
        lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
        regex = r"(?i)^(?:(?:b|br|mlb|msc)\s+)?(\d+)\s*\(?\s*(\d+)\s*\)?\s*(.+)$"
        
        total_pkgs = 0
        for line in lines:
            match = re.search(regex, line)
            if match: total_pkgs += len(match.group(3).split())
            
        if total_pkgs > 1: 
            return await message.reply(" ")
            
        await execute_buy_process(message, lines, regex, 'BR', [DOUBLE_DIAMOND_PACKAGES, BR_PACKAGES], easy_bby.process_smile_one_order, "MLBB")
    except Exception as e: 
        await message.reply(f"System Error: {str(e)}")

@dp.message(F.text.regexp(r"(?i)^(?:mlp|ph|p)\s+\d+"))
async def handle_ph_mlbb(message: types.Message):
    if not await is_authorized(message.from_user.id): 
        return await message.reply(f"ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.❌")
    match = re.search(r"(?i)^(?:msc|mlb|br|b)\s*(\d+)", message.text.strip())
    game_id = match.group(1) if match else None    

    if game_id and str(game_id) in config.GLOBAL_SCAMMERS:
        alert_text = (
            f"<code>{message.text}</code>\n\n"
            f"🚨 <b>Scammer Alert!</b>\n"
            f"ဒီ Game ID (<code>{game_id}</code>) သည် Scammer စာရင်းထဲတွင် ပါဝင်နေပါသဖြင့် ဝယ်ယူခွင့်ကို ပိတ်ပင်ထားပါသည်။ ❌"
        )
        return await message.reply(alert_text, parse_mode=ParseMode.HTML) 
    try:
        lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
        regex = r"(?i)^(?:(?:p|ph|mlp|mcp)\s+)?(\d+)\s*\(?\s*(\d+)\s*\)?\s*(.+)$"
        
        total_pkgs = 0
        for line in lines:
            match = re.search(regex, line)
            if match: total_pkgs += len(match.group(3).split())
            
        if total_pkgs > 1: 
            return await message.reply(" ")
            
        await execute_buy_process(message, lines, regex, 'PH', PH_PACKAGES, easy_bby.process_smile_one_order, "MLBB")
    except Exception as e: 
        await message.reply(f"System Error: {str(e)}")

@dp.message(F.text.regexp(r"(?i)^(?:mcc|mcb)\s+\d+"))
async def handle_br_mcc(message: types.Message):
    if not await is_authorized(message.from_user.id): 
        return await message.reply(f"ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.❌")
    match = re.search(r"(?i)^(?:msc|mlb|br|b)\s*(\d+)", message.text.strip())
    game_id = match.group(1) if match else None    

    if game_id and str(game_id) in config.GLOBAL_SCAMMERS:
        alert_text = (
            f"<code>{message.text}</code>\n\n"
            f"🚨 <b>Scammer Alert!</b>\n"
            f"ဒီ Game ID (<code>{game_id}</code>) သည် Scammer စာရင်းထဲတွင် ပါဝင်နေပါသဖြင့် ဝယ်ယူခွင့်ကို ပိတ်ပင်ထားပါသည်။ ❌"
        )
        return await message.reply(alert_text, parse_mode=ParseMode.HTML)
    try:
        lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
        regex = r"(?i)^(?:(?:mcc|mcb|mcp|mcgg)\s+)?(\d+)\s*\(?\s*(\d+)\s*\)?\s*(.+)$"
        
        total_pkgs = 0
        for line in lines:
            match = re.search(regex, line)
            if match: total_pkgs += len(match.group(3).split())
            
        if total_pkgs > 1: 
            return await message.reply("")
            
        await execute_buy_process(message, lines, regex, 'BR', MCC_PACKAGES, easy_bby.process_mcc_order, "MCC", is_mcc=True)
    except Exception as e: 
        await message.reply(f"System Error: {str(e)}")

@dp.message(F.text.regexp(r"(?i)^mcp\s+\d+"))
async def handle_ph_mcc(message: types.Message):
    if not await is_authorized(message.from_user.id): 
        return await message.reply(f"ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.❌")
    match = re.search(r"(?i)^(?:msc|mlb|br|b)\s*(\d+)", message.text.strip())
    game_id = match.group(1) if match else None    

    if game_id and str(game_id) in config.GLOBAL_SCAMMERS:
        alert_text = (
            f"<code>{message.text}</code>\n\n"
            f"🚨 <b>Scammer Alert!</b>\n"
            f"ဒီ Game ID (<code>{game_id}</code>) သည် Scammer စာရင်းထဲတွင် ပါဝင်နေပါသဖြင့် ဝယ်ယူခွင့်ကို ပိတ်ပင်ထားပါသည်။ ❌"
        )
        return await message.reply(alert_text, parse_mode=ParseMode.HTML)
    try:
        lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
        regex = r"(?i)^(?:mcp\s+)?(\d+)\s*\(?\s*(\d+)\s*\)?\s*(.+)$"
        
        total_pkgs = 0
        for line in lines:
            match = re.search(regex, line)
            if match: total_pkgs += len(match.group(3).split())
            
        if total_pkgs > 1: 
            return await message.reply("")
            
        await execute_buy_process(message, lines, regex, 'PH', PH_MCC_PACKAGES, easy_bby.process_mcc_order, "MCC", is_mcc=True)
    except Exception as e: 
        await message.reply(f"System Error: {str(e)}")

@dp.message(or_f(Command("listb"), F.text.regexp(r"(?i)^\.listb$")))
async def show_price_list_br(message: types.Message):
    if not await is_authorized(message.from_user.id): return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    response_text = f"🇧🇷 <b>𝘿𝙤𝙪𝙗𝙡𝙚 𝙋𝙖𝙘𝙠𝙖𝙜𝙚𝙨</b>\n<code>{generate_list(DOUBLE_DIAMOND_PACKAGES)}</code>\n\n🇧🇷 <b>𝘽𝙧 𝙋𝙖𝙘𝙠𝙖𝙜𝙚𝙨</b>\n<code>{generate_list(BR_PACKAGES)}</code>"
    await message.reply(response_text, parse_mode=ParseMode.HTML)

@dp.message(or_f(Command("listp"), F.text.regexp(r"(?i)^\.listp$")))
async def show_price_list_ph(message: types.Message):
    if not await is_authorized(message.from_user.id): return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    response_text = f"🇵🇭 <b>𝙋𝙝 𝙋𝙖𝙘𝙠𝙖𝙜𝙚𝙨</b>\n<code>{generate_list(PH_PACKAGES)}</code>"
    await message.reply(response_text, parse_mode=ParseMode.HTML)

@dp.message(or_f(Command("listmb"), F.text.regexp(r"(?i)^\.listmb$")))
async def show_price_list_mcc(message: types.Message):
    if not await is_authorized(message.from_user.id): return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    response_text = f"🇧🇷 <b>𝙈𝘾𝘾 𝙋𝘼𝘾𝙆𝘼𝙂𝙀𝙎</b>\n<code>{generate_list(MCC_PACKAGES)}</code>\n\n🇵🇭 <b>𝙋𝙝 𝙈𝘾𝘾 𝙋𝙖𝙘𝙠𝙖𝙜𝙚𝙨</b>\n<code>{generate_list(PH_MCC_PACKAGES)}</code>"
    await message.reply(response_text, parse_mode=ParseMode.HTML)

@dp.message(F.text.regexp(r"^[\d\s\.\(\)]+[\+\-\*\/][\d\s\+\-\*\/\(\)\.]+$"))
async def auto_calculator(message: types.Message):
    try:
        expr = message.text.strip()
        if re.match(r"^09[-\s]?\d+", expr): return
        
        clean_expr = expr.replace(" ", "")
        result = eval(clean_expr, {"__builtins__": None})
        
        if isinstance(result, float): 
            formatted_result = f"{result:.4f}".rstrip('0').rstrip('.')
        else: 
            formatted_result = str(result)

        full_copy_text = f"{expr} = {formatted_result}"

        # သင်အသုံးပြုနေတဲ့ Library ရဲ့ ပုံစံအတိုင်း Copy Button ဖန်တီးခြင်း
        try:
            # Telegram API 7.0+ ရဲ့ တိုက်ရိုက် Copy ကူးပေးတဲ့ Feature
            from aiogram.types import CopyTextButton
            copy_btn = InlineKeyboardButton(
                text=" ᴄᴏᴘʏ", 
                copy_text=CopyTextButton(text=full_copy_text),
                style="danger",
                icon_custom_emoji_id="5456498809875995940"# အရောင်ပါအောင် style ထည့်ခြင်း
            )
        except ImportError:
            # အပေါ်က method အလုပ်မလုပ်ရင် switch_inline သုံးမယ်
            copy_btn = InlineKeyboardButton(
                text=" ᴄᴏᴘʏ", 
                switch_inline_query_current_chat=full_copy_text,
                style="danger",
                icon_custom_emoji_id="5456498809875995940"
            )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[copy_btn]])
        
        # Output ပြသခြင်း
        await message.reply(
            f"<b>{expr} =</b> <code>{formatted_result}</code>", 
            parse_mode="HTML", 
            reply_markup=keyboard
        )
        
    except Exception: 
        pass


@dp.message(or_f(Command("cookies"), F.text.regexp(r"(?i)^\.cookies$")))
async def check_cookie_status(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("❌ You are not authorized.")
    loading_msg = await message.reply("Checking Cookie status...")
    try:
        scraper = await easy_bby.get_main_scraper()
        headers = {'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest', 'Origin': 'https://www.smile.one'}
        response = await scraper.get('https://www.smile.one/customer/order', headers=headers, timeout=15)
        if "login" not in str(response.url).lower() and response.status_code == 200: await loading_msg.edit_text("🟢 Aᴄᴛɪᴠᴇ", parse_mode=ParseMode.HTML)
        else: await loading_msg.edit_text("🔴 Exᴘɪʀᴇᴅ", parse_mode=ParseMode.HTML)
    except Exception as e: await loading_msg.edit_text(f"❌ Error checking cookie: {str(e)}")



@dp.message(or_f(Command("region"), F.text.regexp(r"(?i)^\.region(?:$|\s+)")))
async def handle_check_role(message: types.Message):

    if not await is_authorized(message.from_user.id):
        return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    
    match = re.search(r"(?i)^[./]?region\s+(\d+)\s*[\(]?\s*(\d+)\s*[\)]?", message.text.strip())
    if not match:
        return await message.reply("❌ Invalid format. Use: `.region 12345678 1234`")
    
    game_id, zone_id = match.group(1).strip(), match.group(2).strip()
    loading_msg = await message.reply("<tg-emoji emoji-id='6186254847713484259'>❤️</tg-emoji>", parse_mode=ParseMode.HTML)

    # ⚠️ သင့်ရဲ့ API အသစ် Link ကို ဒီနေရာမှာ အစားထိုးထည့်ပေးပါ
    api_url = 'https://yanjiestore.com/index.php/check-region-mlbb'
    
    # API တောင်းတဲ့ပုံစံပေါ်မူတည်ပြီး 'id' (သို့) 'uid' ပြောင်းသုံးနိုင်ပါတယ်
    payload = {
        'uid': game_id,
        'server': zone_id
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
        'Accept': 'application/json'
    }

    try:
        proxy_dict = get_random_proxy() # Proxy လှမ်းယူမယ်
        
        # ⚠️ ဤနေရာတွင် proxies=proxy_dict ကို ပေါင်းထည့်လိုက်ပါ
        async with AsyncSession(impersonate="chrome124", proxies=proxy_dict) as local_scraper:
            res = await local_scraper.post(api_url, data=payload, headers=headers, timeout=15)
        
        try:
            data = res.json()
        except Exception:
            return await loading_msg.edit_text(
                f"❌ API Error: Invalid Response.\n\n<code>{res.text[:100]}...</code>",
                parse_mode=ParseMode.HTML
            )

        # Status ကို စစ်ဆေးခြင်း
        if not data.get('status'):
            error_msg = data.get('msg') or data.get('message') or "Game ID သို့မဟုတ် Zone ID မှားယွင်းနေပါသည်။"
            return await loading_msg.edit_text(
                f"❌ <b>Invalid Account:</b> <code>{error_msg}</code>",
                parse_mode=ParseMode.HTML
            )

        # JSON အသစ်ပုံစံအရ 'data' အခန်းထဲမှ အချက်အလက်များကို ဆွဲထုတ်ခြင်း
        user_data = data.get('data', {})
        ig_name = user_data.get('nick', 'Unknown')
        country_code = user_data.get('region', 'Unknown')
        
        country_map = {
            # Asia
            "MM": "Myanmar",
            "MY": "Malaysia",
            "PH": "Philippines",
            "ID": "Indonesia",
            "SG": "Singapore",
            "KH": "Cambodia",
            "TH": "Thailand",
            "JP": "Japan",
            "KR": "South Korea",
            "CN": "China",
            "TW": "Taiwan",
            "HK": "Hong Kong",
            "VN": "Vietnam",
            "LA": "Laos",
            "BN": "Brunei",
            "TL": "Timor-Leste",
            "IN": "India",
            "PK": "Pakistan",
            "BD": "Bangladesh",
            "LK": "Sri Lanka",
            "NP": "Nepal",
            "BT": "Bhutan",
            "MV": "Maldives",
            "AF": "Afghanistan",
            "IR": "Iran",
            "IQ": "Iraq",
            "SA": "Saudi Arabia",
            "AE": "United Arab Emirates",
            "QA": "Qatar",
            "KW": "Kuwait",
            "OM": "Oman",
            "YE": "Yemen",
            "JO": "Jordan",
            "LB": "Lebanon",
            "IL": "Israel",
            "SY": "Syria",
            "TR": "Turkey",
            "AZ": "Azerbaijan",
            "GE": "Georgia",
            "AM": "Armenia",
            "KZ": "Kazakhstan",
            "UZ": "Uzbekistan",
            "TM": "Turkmenistan",
            "KG": "Kyrgyzstan",
            "TJ": "Tajikistan",
            "MN": "Mongolia",
            # Europe
            "FR": "France",
            "GB": "United Kingdom",
            "DE": "Germany",
            "IT": "Italy",
            "ES": "Spain",
            "PT": "Portugal",
            "NL": "Netherlands",
            "BE": "Belgium",
            "LU": "Luxembourg",
            "CH": "Switzerland",
            "AT": "Austria",
            "PL": "Poland",
            "CZ": "Czech Republic",
            "SK": "Slovakia",
            "HU": "Hungary",
            "RO": "Romania",
            "BG": "Bulgaria",
            "GR": "Greece",
            "SE": "Sweden",
            "NO": "Norway",
            "FI": "Finland",
            "DK": "Denmark",
            "IS": "Iceland",
            "IE": "Ireland",
            "UA": "Ukraine",
            "BY": "Belarus",
            "LT": "Lithuania",
            "LV": "Latvia",
            "EE": "Estonia",
            "HR": "Croatia",
            "SI": "Slovenia",
            "BA": "Bosnia and Herzegovina",
            "RS": "Serbia",
            "ME": "Montenegro",
            "MK": "North Macedonia",
            "AL": "Albania",
            "MD": "Moldova",
            # Americas
            "BR": "Brazil",
            "US": "United States",
            "CA": "Canada",
            "MX": "Mexico",
            "AR": "Argentina",
            "CL": "Chile",
            "PE": "Peru",
            "CO": "Colombia",
            "VE": "Venezuela",
            "EC": "Ecuador",
            "BO": "Bolivia",
            "PY": "Paraguay",
            "UY": "Uruguay",
            "GY": "Guyana",
            "SR": "Suriname",
            "PA": "Panama",
            "CR": "Costa Rica",
            "NI": "Nicaragua",
            "HN": "Honduras",
            "SV": "El Salvador",
            "GT": "Guatemala",
            "BZ": "Belize",
            "CU": "Cuba",
            "DO": "Dominican Republic",
            "PR": "Puerto Rico",
            "JM": "Jamaica",
            "HT": "Haiti",
            "BS": "Bahamas",
            "TT": "Trinidad and Tobago",
            # Africa
            "ZA": "South Africa",
            "EG": "Egypt",
            "NG": "Nigeria",
            "KE": "Kenya",
            "TZ": "Tanzania",
            "UG": "Uganda",
            "RW": "Rwanda",
            "ET": "Ethiopia",
            "GH": "Ghana",
            "SN": "Senegal",
            "CI": "Ivory Coast",
            "MA": "Morocco",
            "TN": "Tunisia",
            "DZ": "Algeria",
            "LY": "Libya",
            "SD": "Sudan",
            "SS": "South Sudan",
            "ZM": "Zambia",
            "ZW": "Zimbabwe",
            "MW": "Malawi",
            "MZ": "Mozambique",
            "AO": "Angola",
            "NA": "Namibia",
            "BW": "Botswana",
            "MG": "Madagascar",
            "MU": "Mauritius",
            # Oceania
            "AU": "Australia",
            "NZ": "New Zealand",
            "FJ": "Fiji",
            "PG": "Papua New Guinea",
            "SB": "Solomon Islands",
            "VU": "Vanuatu",
            "WS": "Samoa",
            "TO": "Tonga"
        }
        
        final_region = country_map.get(str(country_code).upper(), country_code)

        limit_50 = limit_150 = limit_250 = limit_500 = True 
        
        bonus_limits = user_data.get('rechargeBonus', [])
        for item in bonus_limits:
            title = str(item.get('title', '')).lower()
            is_unavailable = (str(item.get('status', '')).lower() != 'available')
            
            if "50+50" in title:
                limit_50 = is_unavailable
            elif "150+150" in title:
                limit_150 = is_unavailable
            elif "250+250" in title:
                limit_250 = is_unavailable
            elif "500+500" in title:
                limit_500 = is_unavailable

        style_50 = "danger" if limit_50 else "success"
        style_150 = "danger" if limit_150 else "success"
        style_250 = "danger" if limit_250 else "success"
        style_500 = "danger" if limit_500 else "success"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Bᴏɴᴜs 50+50", callback_data="ignore", style=style_50),
                InlineKeyboardButton(text="Bᴏɴᴜs 150+150", callback_data="ignore", style=style_150)
            ],
            [
                InlineKeyboardButton(text="Bᴏɴᴜs 250+250", callback_data="ignore", style=style_250),
                InlineKeyboardButton(text="Bᴏɴᴜs 500+500", callback_data="ignore", style=style_500)
            ]
        ])

        final_report = (
            f"<u><b>Mᴏʙɪʟᴇ Lᴇɢᴇɴᴅs Bᴀɴɢ Bᴀɴɢ</b></u>\n\n"
            f"🆔 <code>{'User ID' :<9}:</code> <code>{game_id}</code> (<code>{zone_id}</code>)\n"
            f"👤 <code>{'Nickname':<9}:</code> {ig_name}\n"
            f"🌍 <code>{'Region'  :<9}:</code> {final_region}\n"
            f"────────────────\n\n"
            f"🎁 <b>Fɪʀsᴛ Rᴇᴄʜᴀʀɢᴇ Bᴏɴᴜs Sᴛᴀᴛᴜs</b>"
        )

        await loading_msg.edit_text(final_report, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        await loading_msg.edit_text(f"❌ System Error: {str(e)}", parse_mode=ParseMode.HTML)

@dp.message(or_f(Command("role"), F.text.regexp(r"(?i)^\.role(?:$|\s+)")))
async def handle_check_role(message: types.Message):

    if not await is_authorized(message.from_user.id): return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
    match = re.search(r"(?i)^[./]?role\s+(\d+)\s*[\(]?\s*(\d+)\s*[\)]?", message.text.strip())
    if not match: return await message.reply("❌ Invalid format. Use: `.role 12345678 1234`")
    
    game_id, zone_id = match.group(1).strip(), match.group(2).strip()
    loading_msg = await message.reply("<tg-emoji emoji-id='6186254847713484259'>❤️</tg-emoji>", parse_mode=ParseMode.HTML)

    # ---------------------------------------------------------
    # ၁။ Caliph Dev API (Name, Region စစ်ဆေးရန်)
    # ---------------------------------------------------------
    url_caliph = 'https://cekidml.caliph.dev/api/validasi'
    params_caliph = {
        'id': game_id,
        'serverid': zone_id
    }
    headers_caliph = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': 'https://cekidml.caliph.dev/',
        'X-Requested-With': 'XMLHttpRequest'
    }

    # ---------------------------------------------------------
    # ၂။ Malsawma Store API (Double Diamond Bonus စစ်ဆေးရန်)
    # ---------------------------------------------------------
    url_malsawma = 'https://www.malsawmastore.in/gadget/doublediamonds_action.php'
    payload_malsawma = {
        'id': game_id,
        'zone': zone_id
    }
    headers_malsawma = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
        'Origin': 'https://www.malsawmastore.in',
        'Referer': 'https://www.malsawmastore.in/gadget/doublediamonds',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }

    try:
        async with AsyncSession(impersonate="safari_ios") as local_scraper:
           
            await local_scraper.get('https://cekidml.caliph.dev/', headers=headers_caliph, timeout=15)
            
            res_caliph, res_malsawma = await asyncio.gather(
                local_scraper.get(url_caliph, params=params_caliph, headers=headers_caliph, timeout=15),
                local_scraper.post(url_malsawma, data=payload_malsawma, headers=headers_malsawma, timeout=15)
            )
        
        ig_name = "Unknown"
        region = "Unknown"

        try:
            data_caliph = res_caliph.json()
            
            if data_caliph.get('status') == 'success':
                result_data = data_caliph.get('result', {})
                ig_name = result_data.get('nickname', 'Unknown')
                region = result_data.get('country', 'Unknown')
            else:
                error_msg = data_caliph.get('message') or data_caliph.get('msg') or "Game ID သို့မဟုတ် Zone ID မှားယွင်းနေပါသည်။"
                return await loading_msg.edit_text(f"❌ <b>Invalid Account:</b> {error_msg}", parse_mode=ParseMode.HTML)

        except Exception as e:
        
            debug_msg = res_caliph.text[:120].replace('<', '&lt;').replace('>', '&gt;').strip()
            return await loading_msg.edit_text(f"❌ **API Error:**\n<code>{debug_msg}...</code>", parse_mode=ParseMode.HTML)


        limit_50 = limit_150 = limit_250 = limit_500 = True 
        debug_bonus_error = ""

        try:
            data_double = res_malsawma.json()
            if str(data_double.get('status', '')).lower() == 'true':
                dd_data = data_double.get('dd', {})
                limit_50 = not dd_data.get('50', False)
                limit_150 = not dd_data.get('150', False)
                limit_250 = not dd_data.get('250', False)
                limit_500 = not dd_data.get('500', False)
            else:
                debug_bonus_error = " <i>(Bonus Data Unavailable)</i>"
        except Exception as e:
            debug_bonus_error = " <i>(Bonus Data Error)</i>"

        # ==========================================
        # (ဂ) Keyboard နှင့် Report ထုတ်ပေးခြင်း
        # ==========================================
        style_50 = "danger" if limit_50 else "success"
        style_150 = "danger" if limit_150 else "success"
        style_250 = "danger" if limit_250 else "success"
        style_500 = "danger" if limit_500 else "success"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Bᴏɴᴜs 50+50", callback_data="ignore", style=style_50),
                InlineKeyboardButton(text="Bᴏɴᴜs 150+150", callback_data="ignore", style=style_150)
            ],
            [
                InlineKeyboardButton(text="Bᴏɴᴜs 250+250", callback_data="ignore", style=style_250),
                InlineKeyboardButton(text="Bᴏɴᴜs 500+500", callback_data="ignore", style=style_500)
            ]
        ])

        final_report = (
            f"<u><b>Mᴏʙɪʟᴇ Lᴇɢᴇɴᴅs Bᴀɴɢ Bᴀɴɢ</b></u>\n\n"
            f"🆔 <code>{'User ID' :<9}:</code> <code>{game_id}</code> (<code>{zone_id}</code>)\n"
            f"👤 <code>{'Nickname':<9}:</code> {ig_name}\n"
            f"🌍 <code>{'Region'  :<9}:</code> {region}\n"
            f"────────────────\n\n"
            f"🎁 <b>Fɪʀsᴛ Rᴇᴄʜᴀʀɢᴇ Bᴏɴᴜs Sᴛᴀᴛᴜs</b>{debug_bonus_error}"
        )

        await loading_msg.edit_text(final_report, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception as e: 
        await loading_msg.edit_text(f"❌ System Error: {str(e)}", parse_mode=ParseMode.HTML)


@dp.message(or_f(Command("checkcus"), Command("cus"), F.text.regexp(r"(?i)^\.(?:checkcus|cus)(?:$|\s+)")))
async def check_official_customer(message: types.Message):
    tg_id = str(message.from_user.id)
    is_owner = (message.from_user.id == OWNER_ID) 
    user_data = await db.get_reseller(tg_id) 
    
    if not is_owner and not user_data:
        return await message.reply("❌ You are not authorized.")
        
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply("⚠️ <b>Usage:</b>\n<code>.cus 12345678</code>\nOr multiple IDs:\n<code>.cus 1234\n5678\n9101</code>", parse_mode=ParseMode.HTML)
        
    search_queries = list(dict.fromkeys(parts[1:]))
    search_set = set(search_queries)
    
    loading_msg = await message.reply(f"🔍 Deep Searching Official Records for <b>{len(search_queries)}</b> IDs...\n<b>(ဆာဗာလုံခြုံရေးအတွက် ၅ စက္ကန့်စီ ခြား၍ ရှာဖွေနေပါသည် ⏳)</b>", parse_mode=ParseMode.HTML)
    
    scraper = await get_main_scraper()
    headers = {'X-Requested-With': 'XMLHttpRequest', 'Origin': 'https://www.smile.one'}
    
    urls_to_check = [
        'https://www.smile.one/customer/activationcode/codelist', 
        'https://www.smile.one/ph/customer/activationcode/codelist',
        'https://www.smile.one/br/customer/activationcode/codelist'
    ]
    
    found_orders = []
    seen_ids = set()
    
    try:
        for api_url in urls_to_check:
            for page_num in range(1, 11): 
                res = await scraper.get(
                    api_url, 
                    params={'type': 'orderlist', 'p': str(page_num), 'pageSize': '50'}, 
                    headers=headers, timeout=15
                )
                try:
                    data = res.json()
                    if 'list' in data and len(data['list']) > 0:
                        for order in data['list']:
                            current_user_id = str(order.get('user_id') or order.get('role_id') or '')
                            order_id = str(order.get('increment_id') or order.get('id') or '')
                            status_val = str(order.get('order_status', '') or order.get('status', '')).lower()
                            

                            if (current_user_id in search_set or order_id in search_set) and status_val in ['success', '1']:
                                if order_id not in seen_ids:
                                    seen_ids.add(order_id)
                                    found_orders.append(order)
                    else: 
                        break 
                except: 
                    break
                
                await asyncio.sleep(5)
                
    except Exception as e: 
        return await loading_msg.edit_text(f"❌ Search Error: {str(e)}", parse_mode=ParseMode.HTML)
        
    txt_content = f"===== OFFICIAL RECORDS SEARCH =====\n"
    txt_content += f"Date: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n"
    txt_content += f"Total IDs Searched: {len(search_queries)}\n"
    txt_content += f"Total Records Found: {len(found_orders)}\n"
    txt_content += "=" * 50 + "\n\n"
    
    for query in search_queries:
        txt_content += f"🔍 Search ID: {query}\n"
        

        orders_for_query = [
            o for o in found_orders 
            if str(o.get('user_id') or o.get('role_id') or '') == query or 
               str(o.get('increment_id') or o.get('id') or '') == query
        ]
        
        if not orders_for_query:
            txt_content += "   ❌ No successful records found.\n"
        else:
            txt_content += f"   ✅ Found {len(orders_for_query)} record(s):\n"
            for idx, order in enumerate(orders_for_query, 1):
                serial_id = str(order.get('increment_id') or order.get('id') or 'Unknown')
                date_str = str(order.get('created_at') or order.get('updated_at') or order.get('create_time') or '')
                currency_sym = str(order.get('total_fee_currency') or '$')
                
                date_display = date_str
                if date_str:
                    try:
                        dt_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                        mmt_dt = dt_obj + datetime.timedelta(hours=9, minutes=30)
                        mm_time_str = mmt_dt.strftime("%I:%M:%S %p") 
                        date_display = f"{date_str} (MM - {mm_time_str})"
                    except:
                        pass

                raw_item_name = str(order.get('product_name') or order.get('goods_name') or order.get('title') or 'Unknown Item')
                raw_item_name = raw_item_name.replace("Mobile Legends BR - ", "").replace("Mobile Legends - ", "").strip()
                
                translations = {
                    "Passe Semanal de Diamante": "Weekly Diamond Pass",
                    "Passagem do crepúsculo": "Twilight Pass",
                    "Passe Crepúsculo": "Twilight Pass",
                    "Pacote Semanal Elite": "Elite Weekly Bundle",
                    "Pacote Mensal Épico": "Epic Monthly Bundle",
                    "Membro Estrela Plus": "Starlight Member Plus",
                    "Membro Estrela": "Starlight Member",
                    "Diamantes": "Diamonds",
                    "Diamante": "Diamond",
                    "Bônus": "Bonus",
                    "Pacote": "Bundle"
                }
                
                for pt, en in translations.items():
                    if pt in raw_item_name:
                        raw_item_name = raw_item_name.replace(pt, en)
                        
                if raw_item_name.endswith(" c") or raw_item_name.endswith(" ("):
                    raw_item_name = raw_item_name[:-2]
                    
                price = str(order.get('price') or order.get('grand_total') or order.get('real_money') or '0.00')
                price_display = f"{price} {currency_sym}" if currency_sym != '$' else f"${price}"
                
                txt_content += f"      [{idx}] {date_display} | {raw_item_name.strip()} ({price_display}) | OrderID: {serial_id}\n"
                
        txt_content += "-" * 50 + "\n"
        
    file_bytes = txt_content.encode('utf-8')
    document = BufferedInputFile(file_bytes, filename=f"Records_Check_{len(search_queries)}_IDs.txt")
    
    caption = f"🎉 <b>Oғғɪᴄɪᴀʟ Rᴇᴄᴏʀᴅs Sᴇᴀʀᴄʜ Cᴏᴍᴘʟᴇᴛᴇᴅ</b>\n\n"
    caption += f"🔍 IDs Checked: <b>{len(search_queries)}</b>\n"
    caption += f"📦 Total Found: <b>{len(found_orders)}</b>\n\n"
    caption += f"<b>(အသေးစိတ်ကို အောက်ပါ .txt ဖိုင်တွင် ဝင်ရောက်ကြည့်ရှုပါ)</b>"
    
    await loading_msg.delete()
    await message.reply_document(
        document=document, 
        caption=caption,
        parse_mode=ParseMode.HTML
    )

@dp.message(or_f(Command("topcus"), F.text.regexp(r"(?i)^\.topcus$")))
async def show_top_customers(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("❌ Only Owner.")
    top_spenders = await db.get_top_customers(limit=10)
    if not top_spenders: return await message.reply("📜 No orders found in database.")
    
    report = "🏆 **Top 10 Customers (By Total Spent)** 🏆\n\n"
    for i, user in enumerate(top_spenders, 1):
        tg_id = user['_id']
        spent = user['total_spent']
        count = user['order_count']
        user_info = await db.get_reseller(tg_id)
        vip_tag = "🌟 [VIP]" if user_info and user_info.get('is_vip') else ""
        report += f"**{i}.** `ID: {tg_id}` {vip_tag}\n💰 Spent: ${spent:,.2f} ({count} Orders)\n\n"
        
    report += "💡 *Use `.setvip <ID>` to grant VIP status.*"
    await message.reply(report)

@dp.message(or_f(Command("setvip"), F.text.regexp(r"(?i)^\.setvip(?:$|\s+)")))
async def grant_vip_status(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("❌ Only Owner.")
    parts = message.text.strip().split()
    if len(parts) < 2: return await message.reply("⚠️ **Usage:** `.setvip <User_ID>`")
    target_id = parts[1]
    user = await db.get_reseller(target_id)
    if not user: return await message.reply("❌ User not found.")
    
    current_status = user.get('is_vip', False)
    new_status = not current_status 
    await db.set_vip_status(target_id, new_status)
    status_msg = "Granted 🌟" if new_status else "Revoked ❌"
    await message.reply(f"✅ VIP Status for `{target_id}` has been **{status_msg}**.")

@dp.message(or_f(Command("sysbal"), F.text.regexp(r"(?i)^\.sysbal$")))
async def check_system_balance(message: types.Message):
    if message.from_user.id != OWNER_ID: return await message.reply("❌ You are not authorized.")
    loading_msg = await message.reply("📊 စနစ်တစ်ခုလုံး၏ မှတ်တမ်းကို တွက်ချက်နေပါသည်...")
    try:
        sys_balances = await db.get_total_system_balances()
        report = (
            "🏦 <b>System V-Wallet Total Balances</b> 🏦\n━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>User အားလုံးဆီရှိ စုစုပေါင်း ငွေကြေး:</b>\n\n"
            f"🇧🇷 BR Balance : <code>${sys_balances['total_br']:,.2f}</code>\n"
            f"🇵🇭 PH Balance : <code>${sys_balances['total_ph']:,.2f}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n<i>(မှတ်ချက်: ဤပမာဏသည် User အားလုံးထံသို့ Admin မှ ထည့်ပေးထားသော လက်ကျန်ငွေများ၏ စုစုပေါင်းဖြစ်ပါသည်။)</i>"
        )
        await loading_msg.edit_text(report, parse_mode=ParseMode.HTML)
    except Exception as e: await loading_msg.edit_text(f"❌ Error calculating system balance: {e}")

@dp.message(or_f(F.text.regexp(r"^\d{7,}(?:\s+\(?\d+\)?)?\s*.*$"), F.caption.regexp(r"^\d{7,}(?:\s+\(?\d+\)?)?\s*.*$")))
async def format_and_copy_text(message: types.Message):
    raw_text = (message.text or message.caption).strip()
    scam_match = re.search(r"^\d{7,}", raw_text)
    game_id = scam_match.group(0) if scam_match else None
    
    if game_id and str(game_id) in config.GLOBAL_SCAMMERS:
        alert_text = (
            f"<code>{raw_text}</code>\n\n"
            f"🚨 <b>Scammer Alert!</b>\n"
            f"ဒီ Game ID (<code>{game_id}</code>) သည် Scammer စာရင်းထဲတွင် ပါဝင်နေပါသဖြင့် ဝယ်ယူခွင့်ကို ပိတ်ပင်ထားပါသည်။ ❌"
        )
        return await message.reply(alert_text, parse_mode="HTML")

    if re.match(r"^\d{7,}$", raw_text): formatted_raw = raw_text
    elif re.match(r"^\d{7,}\s+\d+", raw_text):
        match = re.match(r"^(\d{7,})\s+(\d+)\s*(.*)$", raw_text)
        if match:
            player_id, zone_id, suffix = match.group(1), match.group(2), match.group(3).strip()
            if suffix:
                clean_suffix = suffix.lower().replace(" ", "")
                wp_match = re.match(r"^(\d*)wp(\d*)$", clean_suffix)
                if wp_match:
                    num_str = wp_match.group(1) + wp_match.group(2)
                    processed_suffix = "wp" if num_str in ["", "1"] else f"wp{num_str}"
                else: processed_suffix = suffix
                formatted_raw = f"{player_id} ({zone_id}) {processed_suffix}"
            else: formatted_raw = f"{player_id} ({zone_id})"
        else: formatted_raw = raw_text
    elif re.match(r"^\d{7,}\s*\(\d+\)", raw_text):
        match = re.match(r"^(\d{7,})\s*\((\d+)\)\s*(.*)$", raw_text)
        if match:
            player_id, zone_id, suffix = match.group(1), match.group(2), match.group(3).strip()
            if suffix:
                clean_suffix = suffix.lower().replace(" ", "")
                wp_match = re.match(r"^(\d*)wp(\d*)$", clean_suffix)
                if wp_match:
                    num_str = wp_match.group(1) + wp_match.group(2)
                    processed_suffix = "wp" if num_str in ["", "1"] else f"wp{num_str}"
                else: processed_suffix = suffix
                formatted_raw = f"{player_id} ({zone_id}) {processed_suffix}"
            else: formatted_raw = f"{player_id} ({zone_id})"
        else: formatted_raw = raw_text
    else: formatted_raw = raw_text

    formatted_text = f"<code>{formatted_raw}</code>"
    try:
        from aiogram.types import CopyTextButton
        copy_btn = InlineKeyboardButton(text="ᴄᴏᴘʏ", copy_text=CopyTextButton(text=formatted_raw), style="primary")
    except ImportError:
        copy_btn = InlineKeyboardButton(text="ᴄᴏᴘʏ", switch_inline_query=formatted_raw, style="primary")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[copy_btn]])
    await message.reply(formatted_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

@dp.message(or_f(Command("maintenance"), F.text.regexp(r"(?i)^\.maintenance(?:$|\s+)")))
async def toggle_maintenance(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
        
    parts = message.text.strip().lower().split()
    if len(parts) < 2 or parts[1] not in ["enable", "disable"]:
        return await message.reply("⚠️ <b>Usage:</b> `.maintenance enable` သို့မဟုတ် `.maintenance disable`")
        
    action = parts[1]
    
    if action == "enable":
        config.IS_MAINTENANCE = True
        await message.reply("✅ <b>Maintenance Mode ENABLED.</b>\nယခုအချိန်မှစ၍ Admin မှလွဲ၍ အခြား User များ Bot ကို အသုံးပြု၍ မရတော့ပါ။")
    elif action == "disable":
        config.IS_MAINTENANCE = False
        await message.reply("✅ <b>Maintenance Mode DISABLED.</b>\nBot ကို ပုံမှန်အတိုင်း ပြန်လည်အသုံးပြုနိုင်ပါပြီ။")

@dp.message(or_f(Command("scam"), F.text.regexp(r"(?i)^\.scam(?:$|\s+)")))
async def add_scam_id(message: types.Message):
    if not await is_authorized(message.from_user.id): 
        return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
        
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply("⚠️ <b>Usage:</b> `.scam <Game_ID>`\nဥပမာ: `.scam 123456789`")
        
    scam_id = parts[1].strip()
    if not scam_id.isdigit():
        return await message.reply("❌ Invalid Game ID. ဂဏန်းများသာ ရိုက်ထည့်ပါ။")
        
    await db.add_scammer(scam_id)
    config.GLOBAL_SCAMMERS.add(scam_id)
    
    await message.reply(f"🚨 <b>Scammer ID Added:</b> <code>{scam_id}</code>\n✅ ဤ ID ကို Blacklist သို့ ထည့်သွင်းပြီးပါပြီ။ တွေ့တာနဲ့ Bot မှ အလိုအလျောက် သတိပေးပါတော့မည်။", parse_mode=ParseMode.HTML)

@dp.message(or_f(Command("unscam"), F.text.regexp(r"(?i)^\.unscam(?:$|\s+)")))
async def remove_scam_id(message: types.Message):
    if not await is_authorized(message.from_user.id): 
        return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
        
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply("⚠️ <bb>Usage:</b> `.unscam <Game_ID>`")
        
    scam_id = parts[1].strip()
    
    removed = await db.remove_scammer(scam_id)
    config.GLOBAL_SCAMMERS.discard(scam_id)
    
    if removed:
        await message.reply(f"✅ <b>Scammer ID Removed:</b> <code>{scam_id}</code>\nBlacklist ထဲမှ အောင်မြင်စွာ ဖယ်ရှားလိုက်ပါပြီ။", parse_mode=ParseMode.HTML)
    else:
        await message.reply(f"⚠️ ထို ID သည် Scammer စာရင်းထဲတွင် မရှိပါ။")

@dp.message(or_f(Command("scamlist"), F.text.regexp(r"(?i)^\.scamlist$")))
async def show_scam_list(message: types.Message):
    if not await is_authorized(message.from_user.id): 
        return await message.reply("ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀ.")
        
    if not config.GLOBAL_SCAMMERS:
        return await message.reply("✅ ယခုလောလောဆယ် Blacklist သွင်းထားသော Scammer မရှိပါ။")
        
    scam_text = "\n".join([f"🔸 <code>{sid}</code>" for sid in config.GLOBAL_SCAMMERS])
    await message.reply(f"🚨 <b>Scammer Blacklist (Total: {len(config.GLOBAL_SCAMMERS)}):</b>\n\n{scam_text}", parse_mode=ParseMode.HTML)

@dp.message(or_f(Command("help"), F.text.regexp(r"(?i)^\.help$")))
async def send_help_message(message: types.Message):
    is_owner = (message.from_user.id == OWNER_ID)
    
    help_text = (
        f"<blockquote><b>🤖 𝐁𝐎𝐓 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒 𝐌𝐄𝐍𝐔</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💎 𝐌𝐋𝐁Ｂ 𝐃𝐢𝐚𝐦𝐨𝐧𝐝𝐬 (ဝယ်ယူရန်)</b>\n"
        f"🇧🇷 BR MLBB: <code>msc/mlb/br/b ID (Zone) Pack</code>\n"
        f"🇵🇭 PH MLBB: <code>mlp/ph/p ID (Zone) Pack</code>\n\n"
        f"<b>♟️ 𝐌𝐚𝐠𝐢𝐜 𝐂𝐡𝐞𝐬𝐬 (ဝယ်ယူရန်)</b>\n"
        f"🇧🇷 BR MCC: <code>mcc/mcb ID (Zone) Pack</code>\n"
        f"🇵🇭 PH MCC: <code>mcp ID (Zone) Pack</code>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<b>👤 𝐔𝐬𝐞𝐫 𝐓𝐨𝐨𝐥𝐬 (အသုံးပြုသူများအတွက်)</b>\n"
        f"🔸 <code>.topup Code</code>        : Smile Code ဖြည့်သွင်းရန်\n"
        f"🔹 <code>.bal</code>      : မိမိ Wallet Balance စစ်ရန်\n"
        f"🔹 <code>.role</code>     : Game ID နှင့် Region စစ်ရန်\n"
        f"🔹 <code>.his</code>      : မိမိဝယ်ယူခဲ့သော မှတ်တမ်းကြည့်ရန်\n"
        f"🔹 <code>.clean</code>    : မှတ်တမ်းများ ဖျက်ရန်\n"
        f"🔹 <code>.listb</code>     : BR ဈေးနှုန်းစာရင်း ကြည့်ရန်\n"
        f"🔹 <code>.listp</code>     : PH ဈေးနှုန်းစာရင်း ကြည့်ရန်\n"
        f"🔹 <code>.listmb</code>    : MCC ဈေးနှုန်းစာရင်း ကြည့်ရန်\n"
        f"💡 <i>Tip: 50+50 ဟုရိုက်ထည့်၍ ဂဏန်းပေါင်းစက်အဖြစ် သုံးနိုင်ပါသည်။</i>\n\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🚨 Scammer စီမံခန့်ခွဲမှု</b>\n"
            f"🔸 <code>.scam ID</code>     : Scammer စာရင်းသွင်းရန်\n"
            f"🔸 <code>.unscam ID</code>   : Scammer စာရင်းမှပယ်ဖျက်ရန်\n"
            f"🔸 <code>.scamlist</code>    : Scammer အားလုံးကြည့်ရန်\n\n"
    )
    
    if is_owner:
        help_text += (
            f"\n━━━━━━━━━━━━━━━━━\n"
            f"<b>👑 𝐎𝐰𝐧𝐞𝐫 𝐓𝐨𝐨𝐥𝐬 (Admin သီးသန့်)</b>\n\n"
            f"<b>👥 ယူဆာစီမံခန့်ခွဲမှု</b>\n"
            f"🔸 <code>.maintenance [ᴇɴᴀʙʟᴇ/ᴅɪsᴀʙʟᴇ]</code> : ᴇɴᴀʙʟᴇ ᴏʀ ᴅɪsᴀʙʟᴇ ᴛʜᴇ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ ᴍᴏᴅᴇ ᴏғ ʏᴏᴜʀ ʙᴏᴛ.\n"
            f"🔸 <code>.add ID</code>    : User အသစ်ထည့်ရန်\n"
            f"🔸 <code>.remove ID</code> : User အား ဖယ်ရှားရန်\n"
            f"🔸 <code>.users</code>     : User စာရင်းအားလုံး ကြည့်ရန်\n\n"
            f"🔸 <code>.addbal ID 50 BR</code>  : Balance ပေါင်းထည့်ရန်\n"
            f"🔸 <code>.deduct ID 50 BR</code>  : Balance နှုတ်ယူရန်\n"
            f"<b>💼 VIP နှင့် စာရင်းစစ်</b>\n"
            f"🔸 <code>.checkcus ID</code> : Official မှတ်တမ်း လှမ်းစစ်ရန်\n"
            f"🔸 <code>.topcus</code>      : ငွေအများဆုံးသုံးထားသူများ ကြည့်ရန်\n"
            f"🔸 <code>.setvip ID</code>   : VIP အဖြစ် သတ်မှတ်ရန်/ဖြုတ်ရန်\n\n"
            f"<b>🚨 Scammer စီမံခန့်ခွဲမှု</b>\n"
            f"🔸 <code>.scam ID</code>     : Scammer စာရင်းသွင်းရန်\n"
            f"🔸 <code>.unscam ID</code>   : Scammer စာရင်းမှပယ်ဖျက်ရန်\n"
            f"🔸 <code>.scamlist</code>    : Scammer အားလုံးကြည့်ရန်\n\n"
            f"<b>⚙️ System Setup</b>\n"
            f"🔸 <code>.sysbal</code>      : စနစ်တစ်ခုလုံး၏ Balance စစ်ရန်\n"
            f"🔸 <code>.cookies</code>     : Cookie အခြေအနေ စစ်ဆေးရန်\n"
            f"🔸 <code>/setcookie</code>   : Main Cookie အသစ်ပြောင်းရန်\n"
        )
        
    help_text += f"</blockquote>"
    
    await message.reply(help_text, parse_mode=ParseMode.HTML)

@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    try:
        tg_id = str(message.from_user.id)
        first_name = message.from_user.first_name or ""
        last_name = message.from_user.last_name or ""
        full_name = f"{first_name} {last_name}".strip() or "User"
        safe_full_name = full_name.replace('<', '').replace('>', '')
        username_display = f'<a href="tg://user?id={tg_id}">{safe_full_name}</a>'
        
        EMOJI_1, EMOJI_2, EMOJI_3, EMOJI_4, EMOJI_5 = "6183519108164757054", "5316887736823591263", "5316728625465146646", "5318760565902947324", "5316992572680320646"

        status = "🟢 Aᴄᴛɪᴠᴇ" if await is_authorized(message.from_user.id) else "🔴 Nᴏᴛ Aᴄᴛɪᴠᴇ"
        
        welcome_text = (
            f"ʜᴇʏ ʙᴀʙʏ <tg-emoji emoji-id='{EMOJI_1}'>🥺</tg-emoji>\n\n"
            f"<tg-emoji emoji-id='{EMOJI_2}'>👤</tg-emoji> {'Usᴇʀɴᴀᴍᴇ' :<11}: {username_display}\n"
            f"<tg-emoji emoji-id='{EMOJI_3}'>🆔</tg-emoji> {'𝐈𝐃' :<11}: <code>{tg_id}</code>\n"
            f"<tg-emoji emoji-id='{EMOJI_4}'>📊</tg-emoji> {'Sᴛᴀᴛᴜs' :<11}: {status}\n\n"
            f"<tg-emoji emoji-id='{EMOJI_5}'>📞</tg-emoji> {'Cᴏɴᴛᴀᴄᴛ ᴜs' :<11}: @Julierbo2_151102"
        )
        await message.reply(welcome_text, parse_mode=ParseMode.HTML)
    except Exception:
        fallback_text = (
            f"ʜᴇʏ ʙᴀʙʏ 🥺\n\n"
            f"👤 {'Usᴇʀɴᴀᴍᴇ' :<11}: {full_name}\n"
            f"🆔 {'𝐈𝐃' :<11}: <code>{tg_id}</code>\n"
            f"📊 {'Sᴛᴀᴛᴜs' :<11}: 🔴 Nᴏᴛ Aᴄᴛɪᴠᴇ\n\n"
            f"📞 {'Cᴏɴᴛᴀᴄᴛ ᴜs' :<11}: @Julierbo2_151102"
        )
        await message.reply(fallback_text, parse_mode=ParseMode.HTML)
