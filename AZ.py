#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
آزادکننده – باز کردن قفل ربات‌های دیگر با ارسال /kill
"""

import os, sys, json, time, threading, requests
from typing import List

# ═══════════ تنظیمات ═══════════
MAIN_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN1", "").strip()
if not MAIN_BOT_TOKEN:
    print("ERROR: BALE_BOT_TOKEN not set", file=sys.stderr)
    sys.exit(1)

ADMIN_CHAT_ID = 46829437                     # شناسه عددی ادمین (باید با ربات‌های دیگر یکسان باشد)
BALE_API_URL = "https://tapi.bale.ai/bot"
REQUEST_TIMEOUT = 30
LONG_POLL_TIMEOUT = 50

# ═══════════ توابع کمکی ═══════════
def send_message(token: str, chat_id: int, text: str) -> bool:
    url = f"{BALE_API_URL}{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=REQUEST_TIMEOUT)
        return r.status_code == 200 and r.json().get("ok", False)
    except:
        return False

def get_updates(token: str, offset=None):
    url = f"{BALE_API_URL}{token}/getUpdates"
    params = {"timeout": LONG_POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.post(url, json=params, timeout=LONG_POLL_TIMEOUT + 5)
        if r.status_code == 200 and r.json().get("ok"):
            return r.json()["result"]
    except:
        pass
    return []

# ═══════════ حلقه اصلی ═══════════
def main():
    print("[آزادکننده] آماده دریافت فرمان /azad")
    offset = None
    while True:
        updates = get_updates(MAIN_BOT_TOKEN, offset)
        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message")
            if not msg or "text" not in msg:
                continue
            chat_id = msg["chat"]["id"]
            text = msg["text"].strip()

            # فقط ادمین مجاز است
            if chat_id != ADMIN_CHAT_ID:
                continue

            if text.startswith("/azad"):
                parts = text.split()
                if len(parts) < 2:
                    send_message(MAIN_BOT_TOKEN, chat_id, "❌ فرمت: /azad token1 token2 ...")
                    continue
                tokens = parts[1:]
                send_message(MAIN_BOT_TOKEN, chat_id, f"🔓 در حال ارسال /kill به {len(tokens)} ربات...")
                for token in tokens:
                    if send_message(token, ADMIN_CHAT_ID, "/kill"):
                        print(f"✅ /kill فرستاده شد با توکن {token[:6]}...")
                    else:
                        print(f"❌ خطا در ارسال با توکن {token[:6]}...")
                send_message(MAIN_BOT_TOKEN, chat_id, "✅ پایان عملیات آزادسازی.")
        time.sleep(0.5)

if __name__ == "__main__":
    main()
