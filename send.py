"""
send.py
-------
با repository_dispatch (رویداد send-outage) از سمت Cloudflare Worker اجرا می‌شود.
چون ربات مدیریت عضو گروه‌ها نیست، ارسال واقعی به گروه‌ها با اکانت شخصی (Telethon) انجام می‌شود.

نیاز به این متغیرهای محیطی دارد:
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION   -> اکانت شخصی (Telethon)
  PAYLOAD                                                 -> JSON شامل {"text": ..., "chat_ids": [...]}
"""

import json
import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.environ["TELEGRAM_SESSION"]
PAYLOAD = json.loads(os.environ["PAYLOAD"])

TEXT = PAYLOAD["text"]
CHAT_IDS = PAYLOAD["chat_ids"]


def main():
    with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        for chat_id in CHAT_IDS:
            try:
                client.send_message(int(chat_id), TEXT, parse_mode="html")
            except Exception as e:
                print(f"خطا در ارسال به {chat_id}: {e}")


if __name__ == "__main__":
    main()
