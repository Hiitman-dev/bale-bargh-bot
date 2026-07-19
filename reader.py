"""
reader.py
---------
اجرا می‌شود توسط GitHub Actions (هر چند دقیقه). با اکانت شخصی تلگرام (Telethon):

۱. پیام‌های جدید ربات منبع خاموشی برق را می‌خواند، فقط پیام‌های واقعی خاموشی را
   تشخیص می‌دهد، تاریخ/ساعت را استخراج می‌کند و در قالب جدید (درشت) قرار می‌دهد.
۲. هر پیام آماده را با یک درخواست HTTP به ربات مدیریت (روی Cloudflare Worker) می‌فرستد.
۳. دستور /activate <CODE> در گروه‌ها را تشخیص می‌دهد و گروه را هم به Worker
   ثبت می‌کند و هم به‌صورت محلی نگه می‌دارد تا دوباره ثبت نشود.

نکته: اولین اجرا فقط baseline پیام‌ها را ثبت می‌کند و چیزی نمی‌فرستد،
تا کل تاریخچه‌ی قدیمی به‌عنوان "پیام جدید" سیل نشود.

نیاز به این متغیرهای محیطی دارد:
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION   -> اکانت شخصی (Telethon)
  SOURCE_BOT_USERNAME                                     -> یوزرنیم ربات منبع خاموشی (بدون @)
  ACTIVATION_CODE                                         -> کد فعال‌سازی گروه‌ها
  WORKER_BASE_URL                                         -> مثلا https://bargh-manager.xxx.workers.dev
  WORKER_SHARED_SECRET                                    -> باید دقیقاً با READER_SECRET روی Worker یکی باشد
"""

import json
import os
import re

import requests
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Chat, Channel

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.environ["TELEGRAM_SESSION"]
SOURCE_BOT = os.environ.get("SOURCE_BOT_USERNAME", "Bargheman1_bot")
ACTIVATION_CODE = os.environ["ACTIVATION_CODE"]
WORKER_BASE_URL = os.environ["WORKER_BASE_URL"].rstrip("/")
WORKER_SECRET = os.environ["WORKER_SHARED_SECRET"]

STATE_FILE = "state.json"
GROUPS_FILE = "groups.json"  # فقط رکورد محلی، برای جلوگیری از ثبت تکراری یک گروه

OUTAGE_PATTERN = re.compile(r"خاموشی\s*(برنامه‌ریزی‌شده|رخ‌داده|رخداده)")
DATETIME_PATTERN = re.compile(
    r"•\s*(?P<date>\d{4}/\d{2}/\d{2})\s*\|\s*(?P<start>\d{2}:\d{2})\s*تا\s*(?P<end>\d{2}:\d{2})"
)

TEMPLATE = (
    "⚡️ <b>اطلاعیه خاموشی برق</b>\n\n"
    "🗓 <b>تاریخ:</b> {date}\n"
    "⏰ <b>ساعت:</b> {start} تا {end}"
)


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def format_message(raw_text):
    m = DATETIME_PATTERN.search(raw_text)
    if not m:
        return None  # خاموشیه ولی تاریخ/ساعتش با فرمت شناخته‌شده مطابقت نداره
    return TEMPLATE.format(**m.groupdict())


def send_pending_to_worker(text):
    r = requests.post(
        f"{WORKER_BASE_URL}/api/pending",
        headers={"X-Reader-Secret": WORKER_SECRET},
        json={"text": text},
        timeout=15,
    )
    r.raise_for_status()


def register_group_with_worker(chat_id, title):
    r = requests.post(
        f"{WORKER_BASE_URL}/api/groups",
        headers={"X-Reader-Secret": WORKER_SECRET},
        json={"chat_id": str(chat_id), "title": title},
        timeout=15,
    )
    r.raise_for_status()


def poll_source(client, state):
    source_entity = client.get_entity(SOURCE_BOT)
    first_run = "last_source_id" not in state
    last_id = state.get("last_source_id", 0)
    new_last_id = last_id
    formatted_texts = []

    for msg in client.iter_messages(source_entity, min_id=last_id, reverse=True):
        if msg.id > new_last_id:
            new_last_id = msg.id
        if first_run:
            continue  # اولین اجرا: فقط baseline ثبت می‌شه، پیام قدیمی فرستاده نمی‌شه
        if not msg.text or not OUTAGE_PATTERN.search(msg.text):
            continue
        formatted = format_message(msg.text)
        if formatted:
            formatted_texts.append(formatted)

    state["last_source_id"] = new_last_id
    return formatted_texts


def poll_group_activations(client, state, groups):
    last_group_ids = state.get("last_group_ids", {})
    for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, (Chat, Channel)):
            continue
        if isinstance(entity, Channel) and not entity.megagroup:
            continue

        chat_id = str(dialog.id)
        since_id = last_group_ids.get(chat_id, 0)
        newest_id = since_id

        for msg in client.iter_messages(dialog.entity, min_id=since_id, reverse=True, limit=50):
            if msg.id > newest_id:
                newest_id = msg.id
            text = (msg.text or "").strip()
            if text == f"/activate {ACTIVATION_CODE}" and chat_id not in groups:
                groups[chat_id] = dialog.name
                register_group_with_worker(chat_id, dialog.name)
                client.send_message(dialog.entity, "✅ این گروه با موفقیت برای دریافت اطلاع‌رسانی خاموشی برق فعال شد.")

        last_group_ids[chat_id] = newest_id

    state["last_group_ids"] = last_group_ids


def main():
    state = load_json(STATE_FILE, {})
    groups = load_json(GROUPS_FILE, {})

    with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        new_texts = poll_source(client, state)
        poll_group_activations(client, state, groups)

    for text in new_texts:
        send_pending_to_worker(text)

    save_json(STATE_FILE, state)
    save_json(GROUPS_FILE, groups)


if __name__ == "__main__":
    main()
