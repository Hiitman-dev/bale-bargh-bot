"""
forwarder.py
--------------
با اکانت شخصی تلگرام شما (نه یک بات) اجرا می‌شود:

۱. اگر پیام جدیدی از @Bargheman1_bot در چت خصوصی‌تان برسد، آن را عیناً
   به تمام گروه‌های ثبت‌شده فوروارد می‌کند.
۲. اگر در یک گروه پیام "/activate <ACTIVATION_CODE>" دیده شود، آن گروه
   را در groups.json ثبت می‌کند (و پیام تایید می‌فرستد).

توسط GitHub Actions هر چند دقیقه یک‌بار اجرا می‌شود (نه به‌صورت دائم روشن).
هر بار فقط پیام‌های جدید از آخرین اجرای قبلی را پردازش می‌کند (با ذخیره last_id).
"""

import json
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Chat, Channel

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.environ["TELEGRAM_SESSION"]
ACTIVATION_CODE = os.environ["ACTIVATION_CODE"]
SOURCE_BOT = "Bargheman1_bot"  # منبع پیام‌های خاموشی

GROUPS_FILE = "groups.json"
STATE_FILE = "state.json"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    groups = load_json(GROUPS_FILE, {})       # {"<chat_id>": "<title>"}
    state = load_json(STATE_FILE, {"last_source_id": 0, "last_group_ids": {}})

    with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:

        # ---------- ۱. فوروارد پیام‌های جدید از ربات برق‌من ----------
        source_entity = client.get_entity(SOURCE_BOT)
        new_last_id = state["last_source_id"]

        messages = list(client.iter_messages(source_entity, min_id=state["last_source_id"], reverse=True))
        for msg in messages:
            if msg.id > new_last_id:
                new_last_id = msg.id
            if not msg.text:
                continue
            for chat_id in groups.keys():
                try:
                    client.send_message(int(chat_id), msg.text)
                except Exception as e:
                    print(f"خطا در ارسال به گروه {chat_id}: {e}")

        state["last_source_id"] = new_last_id

        # ---------- ۲. بررسی پیام‌های "/activate" در گروه‌ها ----------
        last_group_ids = state.get("last_group_ids", {})

        for dialog in client.iter_dialogs():
            entity = dialog.entity
            if not isinstance(entity, (Chat, Channel)):
                continue
            if isinstance(entity, Channel) and not entity.megagroup:
                continue  # کانال معمولی را نادیده بگیر، فقط گروه/سوپرگروه

            chat_id = str(dialog.id)
            since_id = last_group_ids.get(chat_id, 0)
            newest_id = since_id

            group_messages = list(client.iter_messages(dialog.entity, min_id=since_id, reverse=True, limit=50))
            for msg in group_messages:
                if msg.id > newest_id:
                    newest_id = msg.id
                text = (msg.text or "").strip()
                if text == f"/activate {ACTIVATION_CODE}" and chat_id not in groups:
                    groups[chat_id] = dialog.name
                    client.send_message(dialog.entity, "✅ این گروه با موفقیت برای دریافت اطلاع‌رسانی خاموشی برق فعال شد.")
                    print(f"گروه جدید ثبت شد: {dialog.name} ({chat_id})")

            last_group_ids[chat_id] = newest_id

        state["last_group_ids"] = last_group_ids

    save_json(GROUPS_FILE, groups)
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
