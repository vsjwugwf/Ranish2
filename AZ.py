#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
آزادکننده – نسخهٔ خودکار
توکن‌های ربات‌های قفل‌شده را از متغیر محیطی TARGET_BOT_TOKENS می‌گیرد
و به هر کدام /kill می‌فرستد.
"""

import os, sys, requests

ADMIN_CHAT_ID = 46829437
BALE_API_URL = "https://tapi.bale.ai/bot"
TIMEOUT = 10

def send_kill(token: str) -> bool:
    url = f"{BALE_API_URL}{token}/sendMessage"
    payload = {"chat_id": ADMIN_CHAT_ID, "text": "/kill"}
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        ok = r.status_code == 200 and r.json().get("ok", False)
        return ok
    except Exception as e:
        print(f"❌ خطا در ارسال به {token[:8]}... : {e}")
        return False

def main():
    tokens_str = os.getenv("TARGET_BOT_TOKENS", "").strip()
    if not tokens_str:
        print("❌ متغیر TARGET_BOT_TOKENS تنظیم نشده است.")
        sys.exit(1)

    tokens = [t.strip() for t in tokens_str.split(",") if t.strip()]
    if not tokens:
        print("❌ هیچ توکن معتبری یافت نشد.")
        sys.exit(1)

    print(f"🔓 در حال ارسال /kill به {len(tokens)} ربات...")
    success = 0
    for token in tokens:
        if send_kill(token):
            print(f"✅ /kill با موفقیت به {token[:8]}... ارسال شد.")
            success += 1
        else:
            print(f"⚠️ ارسال به {token[:8]}... ناموفق بود.")

    print(f"\n🎯 عملیات پایان یافت. {success} از {len(tokens)} ربات آزاد شدند.")
    if success != len(tokens):
        sys.exit(1)   # تا workflow با خطا مواجه شود و شما مطلع شوید

if __name__ == "__main__":
    main()
