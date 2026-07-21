"""
reader.py
---------
اجرا می‌شود توسط GitHub Actions (هر ۳۰ دقیقه، برای موندن زیر سقف رایگان دقیقه‌های Actions). با اکانت شخصی تلگرام (Telethon):

۱. آخرین پیام‌های ربات منبع خاموشی برق را می‌خواند. تشخیص «پیام جدیده یا نه» بر اساس
   خودِ محتوا (تاریخ+ساعت استخراج‌شده) انجام می‌شه، نه شناسه‌ی پیام — چون با عوض شدن
   منبع پیام در طول این پروژه، شناسه‌ها دیگه قابل‌اعتماد نبودن. یعنی اگه دقیقاً همون
   تاریخ/ساعت قبلاً فرستاده شده باشه، دوباره نمی‌فرسته؛ اگه جدید باشه، می‌فرسته.
۲. هر پیام تازه را با یک درخواست HTTP به ربات مدیریت (روی Cloudflare Worker) می‌فرستد.
۳. دستور /activate <CODE> در گروه‌ها را تشخیص می‌دهد (روش قدیمی/اختیاری؛ روش اصلی الان
   خود ربات مدیریته که مستقیم عضو گروه‌هاست).
۴. در ساعت‌های مشخص‌شده در STATUS_QUERY_TIMES (به وقت تهران)، خودش دستور /status را
   به ربات منبع می‌فرستد و جواب را هم از همون مسیر تشخیص تکراری/جدید رد می‌کنه.

نکته: اولین اجرا (وقتی state.json هنوز sent_fingerprints نداره) فقط baseline پیام‌های
موجود رو ثبت می‌کنه و چیزی نمی‌فرسته، تا تاریخچه‌ی قدیمی به‌عنوان "پیام جدید" سیل نشه.

نیاز به این متغیرهای محیطی دارد:
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION   -> اکانت شخصی (Telethon)
  SOURCE_BOT_USERNAME                                     -> یوزرنیم ربات منبع خاموشی (بدون @)
  ACTIVATION_CODE                                         -> کد فعال‌سازی گروه‌ها (روش قدیمی)
  WORKER_BASE_URL                                         -> مثلا https://bargh-manager.xxx.workers.dev
  WORKER_SHARED_SECRET                                    -> باید دقیقاً با READER_SECRET روی Worker یکی باشد
  STATUS_QUERY_TIMES                                      -> اختیاری. مثلا "07:00,13:00,20:00" (به وقت تهران)
"""

import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

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
STATUS_QUERY_TIMES = [t.strip() for t in os.environ.get("STATUS_QUERY_TIMES", "").split(",") if t.strip()]

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

STATE_FILE = "state.json"
GROUPS_FILE = "groups.json"  # فقط رکورد محلی، برای جلوگیری از ثبت تکراری یک گروه

# تنها معیار تشخیص «این پیام یه اطلاعیه‌ی خاموشیه» همینه — یه الگوی خیلی مشخص و کم‌ابهام،
# به‌جای اینکه به عبارت‌های فارسی‌ای مثل "برنامه‌ریزی‌شده" وابسته باشیم که با نیم‌فاصله/فاصله
# ممکنه به‌شکل‌های مختلف نوشته بشن و match رو بی‌صدا خراب کنن.
DATETIME_PATTERN = re.compile(
    r"•\s*(?P<date>\d{4}/\d{2}/\d{2})\s*\|\s*(?P<start>\d{2}:\d{2})\s*تا\s*(?P<end>\d{2}:\d{2})"
)

TEMPLATE = (
    "⚡️ <b>اطلاعیه خاموشی برق</b>\n\n"
    "🗓 <b>تاریخ:</b> {date}\n"
    "⏰ <b>ساعت:</b> {start} تا {end}"
)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"⚠️ فایل {path} خراب/نامعتبره ({e})، به‌جاش از مقدار پیش‌فرض شروع می‌کنم.")
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def outage_fingerprint(date, start, end):
    return f"{date}|{start}|{end}"


def prune_fingerprints(sent_fps, keep=200):
    if len(sent_fps) <= keep:
        return
    for k in list(sent_fps.keys())[: len(sent_fps) - keep]:
        del sent_fps[k]


def consider_text(raw_text, state, new_texts, first_run=False):
    """اگه متن شامل یه تاریخ/ساعت خاموشی معتبر بود و قبلاً فرستاده نشده، فرمتش کن
    و به لیست ارسال اضافه کن. برمی‌گردونه که آیا چیزی جدید اضافه شد یا نه."""
    m = DATETIME_PATTERN.search(raw_text or "")
    if not m:
        return False
    fp = outage_fingerprint(**m.groupdict())
    sent_fps = state.setdefault("sent_fingerprints", {})
    if fp in sent_fps:
        return False  # دقیقاً همین تاریخ/ساعت قبلاً فرستاده شده
    if first_run:
        sent_fps[fp] = "baseline"
        return False
    new_texts.append(TEMPLATE.format(**m.groupdict()))
    sent_fps[fp] = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M")
    return True


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


def poll_source(client, source_entity, state, new_texts):
    first_run = "sent_fingerprints" not in state
    checked = added = 0

    for msg in client.iter_messages(source_entity, limit=20):
        checked += 1
        if consider_text(msg.text, state, new_texts, first_run=first_run):
            added += 1

    prune_fingerprints(state.setdefault("sent_fingerprints", {}))
    tag = "اولین اجرا (فقط baseline)" if first_run else "بررسی معمول"
    print(f"📡 [{tag}] {checked} پیام اخیر از {SOURCE_BOT} بررسی شد، {added} خاموشی جدید پیدا شد.")


def due_status_check_times(state):
    """کدوم زمان‌های تنظیم‌شده امروز (به وقت تهران) گذشتن و هنوز چک نشدن؟
    به‌عنوان side effect، بلافاصله در state به‌عنوان چک‌شده‌ی امروز علامت می‌زنه.

    عمداً پنجره‌ی زمانی محدود نداره (فقط >= زمان هدف)، چون گیت‌هاب اکشنز
    زمان‌بندی‌های schedule رو گاهی با تاخیر قابل‌توجه (۱۰-۲۰+ دقیقه) اجرا می‌کنه؛
    یه پنجره‌ی باریک باعث می‌شد در تاخیرهای طولانی، اون روز کلاً رد بشه."""
    now = datetime.now(TEHRAN_TZ)

    if not STATUS_QUERY_TIMES:
        print("⏱ STATUS_QUERY_TIMES تنظیم نشده (یا خالیه) — این قابلیت غیرفعاله.")
        return []

    today = now.strftime("%Y-%m-%d")
    sent = state.setdefault("status_sent", {})
    due = []

    print(f"⏱ زمان الان به وقت تهران: {now.strftime('%H:%M')} — ساعت‌های تنظیم‌شده: {STATUS_QUERY_TIMES}")

    for t in STATUS_QUERY_TIMES:
        try:
            hh, mm = (int(x) for x in t.split(":"))
        except ValueError:
            print(f"   ⚠️ «{t}» فرمت درستی نیست، باید HH:MM باشه (مثلا 07:00).")
            continue
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if sent.get(t) == today:
            print(f"   {t}: امروز قبلاً چک شده، دوباره لازم نیست.")
        elif now < target:
            print(f"   {t}: هنوز نرسیده.")
        else:
            print(f"   {t}: سررسیده و امروز چک نشده -> الان /status می‌فرستم.")
            due.append(t)
            sent[t] = today

    return due


def query_status(client, source_entity):
    """دستور /status رو به ربات منبع می‌فرسته و منتظر جواب می‌مونه."""
    sent_msg = client.send_message(source_entity, "/status")
    time.sleep(8)
    for reply in client.get_messages(source_entity, min_id=sent_msg.id, limit=5):
        if reply.out:
            continue  # پیام خودمون رو نادیده بگیر، فقط جواب ربات مهمه
        return reply.text
    return None


def poll_group_activations(client, state, groups):
    last_group_ids = state.get("last_group_ids", {})
    checked_dialogs = activated_now = 0

    for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, (Chat, Channel)):
            continue
        if isinstance(entity, Channel) and not entity.megagroup:
            continue
        checked_dialogs += 1

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
                activated_now += 1

        last_group_ids[chat_id] = newest_id

    state["last_group_ids"] = last_group_ids
    print(f"👥 {checked_dialogs} گروه/سوپرگروه بررسی شد، {activated_now} گروه تازه فعال شد.")


def main():
    state = load_json(STATE_FILE, {})
    groups = load_json(GROUPS_FILE, {})
    new_texts = []

    with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        source_entity = client.get_entity(SOURCE_BOT)
        print(f"✅ به {SOURCE_BOT} وصل شد (id={source_entity.id}).")

        poll_source(client, source_entity, state, new_texts)
        poll_group_activations(client, state, groups)

        due_times = due_status_check_times(state)
        for t in due_times:
            print(f"⏱ زمان چک وضعیت رسیده: {t} — در حال فرستادن /status ...")
            reply_text = query_status(client, source_entity)
            if reply_text is None:
                print("   ⚠️ جوابی از ربات دریافت نشد.")
            elif consider_text(reply_text, state, new_texts):
                print("   ✅ خاموشی جدید در پاسخ /status پیدا شد.")
            else:
                print("   ℹ️ پاسخ /status چیز جدیدی نداشت (یا تکراری بود).")

    print(f"📨 {len(new_texts)} پیام آماده‌ی فرستادن به Worker.")
    for text in new_texts:
        try:
            send_pending_to_worker(text)
            print("   ➡️ با موفقیت به Worker فرستاده شد.")
        except Exception as e:
            print(f"   ❌ خطا در فرستادن به Worker: {e}")

    save_json(STATE_FILE, state)
    save_json(GROUPS_FILE, groups)
    print("💾 state.json و groups.json ذخیره شدن.")


if __name__ == "__main__":
    main()
