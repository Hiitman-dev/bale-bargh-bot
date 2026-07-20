"""
reader.py
---------
اجرا می‌شود توسط GitHub Actions (هر چند دقیقه). با اکانت شخصی تلگرام (Telethon):

۱. پیام‌های جدید ربات منبع خاموشی برق را می‌خواند، فقط پیام‌های واقعی خاموشی را
   تشخیص می‌دهد، تاریخ/ساعت را استخراج می‌کند و در قالب جدید (درشت) قرار می‌دهد.
۲. هر پیام آماده را با یک درخواست HTTP به ربات مدیریت (روی Cloudflare Worker) می‌فرستد.
۳. دستور /activate <CODE> در گروه‌ها را تشخیص می‌دهد و گروه را هم به Worker
   ثبت می‌کند و هم به‌صورت محلی نگه می‌دارد تا دوباره ثبت نشود.
۴. در ساعت‌های مشخص‌شده در STATUS_QUERY_TIMES (به وقت تهران)، خودش دستور /status را
   به ربات منبع می‌فرستد و اگر جواب شامل تاریخ/ساعت خاموشی بود، همان را هم فوروارد می‌کند.

نکته: اولین اجرا فقط baseline پیام‌ها را ثبت می‌کند و چیزی نمی‌فرستد،
تا کل تاریخچه‌ی قدیمی به‌عنوان "پیام جدید" سیل نشود.

نیاز به این متغیرهای محیطی دارد:
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION   -> اکانت شخصی (Telethon)
  SOURCE_BOT_USERNAME                                     -> یوزرنیم ربات منبع خاموشی (بدون @)
  ACTIVATION_CODE                                         -> کد فعال‌سازی گروه‌ها
  WORKER_BASE_URL                                         -> مثلا https://bargh-manager.xxx.workers.dev
  WORKER_SHARED_SECRET                                    -> باید دقیقاً با READER_SECRET روی Worker یکی باشد
  STATUS_QUERY_TIMES                                      -> اختیاری. مثلا "07:00,13:00,20:00" (به وقت تهران)
"""

import json
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

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
STATUS_QUERY_TIMES = [t.strip() for t in os.environ.get("STATUS_QUERY_TIMES", "").split(",") if t.strip()]

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

STATE_FILE = "state.json"
GROUPS_FILE = "groups.json"  # فقط رکورد محلی، برای جلوگیری از ثبت تکراری یک گروه

# هر دو فرمتی که ربات منبع می‌فرسته رو پوشش می‌ده:
#   فرمت ۱: "⚠️ هشدار خاموشی برق" + خطوط "📅 تاریخ:" و "⏰ ساعت: ... تا ..."
#   فرمت ۲: "⚠️ خاموشی برنامه‌ریزی‌شده/رخ‌داده" + خط "• تاریخ | ساعت تا ساعت"
OUTAGE_PATTERN = re.compile(r"⚠️.*?(هشدار خاموشی برق|خاموشی\s*(برنامه‌ریزی‌شده|رخ‌داده|رخداده))")

DATE_PATTERN = re.compile(r"(?P<date>\d{4}/\d{2}/\d{2})")
TIME_PATTERN = re.compile(r"(?P<start>\d{2}:\d{2})\s*تا\s*(?P<end>\d{2}:\d{2})")
ADDRESS_PATTERN = re.compile(r"📍\s*(?:آدرس:\s*)?(?P<address>.+)")
LABEL_PATTERN = re.compile(r"🏠\s*(?:قبض:\s*)?(?P<label>.+)")
TYPE_PATTERN = re.compile(r"(?:📌\s*نوع:\s*|⚠️\s*خاموشی\s*)(?P<type>برنامه‌ریزی‌شده|رخ‌داده|رخداده)")

TEMPLATE = (
    "⚡️ <b>اطلاعیه خاموشی برق</b>\n\n"
    "🏠 <b>قبض:</b> {label}\n"
    "📌 <b>نوع:</b> {type}\n"
    "🗓 <b>تاریخ:</b> {date}\n"
    "⏰ <b>ساعت:</b> {start} تا {end}\n"
    "📍 <b>آدرس:</b> {address}"
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
    date_m = DATE_PATTERN.search(raw_text)
    time_m = TIME_PATTERN.search(raw_text)
    if not date_m or not time_m:
        return None  # خاموشیه ولی تاریخ/ساعتش قابل استخراج نبود

    address_m = ADDRESS_PATTERN.search(raw_text)
    label_m = LABEL_PATTERN.search(raw_text)
    type_m = TYPE_PATTERN.search(raw_text)

    return TEMPLATE.format(
        label=label_m.group("label").strip() if label_m else "-",
        type=type_m.group("type") if type_m else "-",
        date=date_m.group("date"),
        start=time_m.group("start"),
        end=time_m.group("end"),
        address=address_m.group("address").strip() if address_m else "-",
    )


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


def poll_source(client, source_entity, state):
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


def due_status_check_times(state):
    """کدوم زمان‌های تنظیم‌شده الان (±۵ دقیقه، به وقت تهران) سررسیدن و امروز هنوز چک نشدن؟
    به‌عنوان side effect، بلافاصله در state به‌عنوان چک‌شده‌ی امروز علامت می‌زنه."""
    if not STATUS_QUERY_TIMES:
        return []

    now = datetime.now(TEHRAN_TZ)
    today = now.strftime("%Y-%m-%d")
    sent = state.setdefault("status_sent", {})
    due = []

    for t in STATUS_QUERY_TIMES:
        try:
            hh, mm = (int(x) for x in t.split(":"))
        except ValueError:
            continue
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        diff_minutes = (now - target).total_seconds() / 60
        if 0 <= diff_minutes < 5 and sent.get(t) != today:
            due.append(t)
            sent[t] = today

    return due


def query_status(client, source_entity):
    """دستور /status رو به ربات منبع می‌فرسته و منتظر جواب می‌مونه."""
    sent_msg = client.send_message(source_entity, "/status")
    time.sleep(5)
    for reply in client.get_messages(source_entity, min_id=sent_msg.id, limit=5):
        if reply.out:
            continue  # پیام خودمون رو نادیده بگیر، فقط جواب ربات مهمه
        return reply.text
    return None


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
        source_entity = client.get_entity(SOURCE_BOT)

        new_texts = poll_source(client, source_entity, state)
        poll_group_activations(client, state, groups)

        due_times = due_status_check_times(state)
        for t in due_times:
            reply_text = query_status(client, source_entity)
            if reply_text:
                formatted = format_message(reply_text)
                if formatted:
                    new_texts.append(formatted)

    for text in new_texts:
        send_pending_to_worker(text)

    save_json(STATE_FILE, state)
    save_json(GROUPS_FILE, groups)


if __name__ == "__main__":
    main()
