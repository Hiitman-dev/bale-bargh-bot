"""
main.py
---------
یک اسکریپت که هر چند دقیقه (توسط GitHub Actions) اجرا می‌شود و این کارها را انجام می‌دهد:

۱. با اکانت شخصی تلگرام شما (Telethon) پیام‌های جدید @Bargheman1_bot را می‌خواند.
   فقط پیام‌هایی که واقعاً "خاموشی برنامه‌ریزی‌شده" یا "خاموشی رخ‌داده" هستند را در نظر می‌گیرد.
   برای هرکدام یک "پیام در انتظار تایید" (pending) می‌سازد.

۲. با ربات مدیریت (BotFather، Bot API معمولی) که فقط شما ادمینش هستید:
   - برای هر پیام در انتظار، پیش‌نمایش + دکمه‌های [✅ ارسال] [✏️ ویرایش] [❌ لغو] برایتان می‌فرستد
   - دستورات مدیریتی شما را پردازش می‌کند: /groups /setdefault /autosend /timeout /history /status

۳. اگر ارسال خودکار روشن باشد و ظرف مدت timeout جوابی ندهید، خودش پیام را
   به گروه‌های پیش‌فرض می‌فرستد.

۴. پیام‌های ارسال‌شده را در history.json ثبت می‌کند.

نیاز به این متغیرهای محیطی دارد:
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION   -> اکانت شخصی (Telethon)
  MANAGER_BOT_TOKEN                                       -> توکن ربات مدیریت (از BotFather)
  ADMIN_USER_ID                                           -> آیدی عددی تلگرام شما
  ACTIVATION_CODE                                         -> کد فعال‌سازی گروه‌ها
"""

import json
import os
import re
import time
import requests
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Chat, Channel

# ---------- تنظیمات از GitHub Secrets ----------
API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.environ["TELEGRAM_SESSION"]
MANAGER_TOKEN = os.environ["MANAGER_BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_USER_ID"])
ACTIVATION_CODE = os.environ["ACTIVATION_CODE"]
SOURCE_BOT = "Bargheman1_bot"

MANAGER_API = f"https://api.telegram.org/bot{MANAGER_TOKEN}"

# ---------- فایل‌های وضعیت (در ریپو نگه‌داری می‌شوند) ----------
GROUPS_FILE = "groups.json"        # {"<chat_id>": "<title>"}
STATE_FILE = "state.json"          # آخرین id های پردازش‌شده
SETTINGS_FILE = "settings.json"    # تنظیمات ارسال خودکار
PENDING_FILE = "pending.json"      # پیام‌های در انتظار تایید
HISTORY_FILE = "history.json"      # پیام‌های ارسال‌شده
MGR_OFFSET_FILE = "mgr_offset.json"  # offset دستورات ربات مدیریت

OUTAGE_PATTERN = re.compile(r"خاموشی\s*(برنامه‌ریزی‌شده|رخ‌داده|رخداده)")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def mgr_call(method, **params):
    r = requests.post(f"{MANAGER_API}/{method}", json=params, timeout=15)
    return r.json()


def send_to_admin(text, reply_markup=None):
    return mgr_call("sendMessage", chat_id=ADMIN_ID, text=text, reply_markup=reply_markup)


def group_list_text(groups):
    if not groups:
        return "هنوز هیچ گروهی ثبت نشده."
    lines = []
    for i, (chat_id, title) in enumerate(groups.items(), start=1):
        lines.append(f"{i}. {title}  (id: {chat_id})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# بخش ۱: خواندن پیام‌های جدید ربات برق‌من + ثبت گروه با کد فعال‌سازی
# ---------------------------------------------------------------------------
def poll_source_and_groups(groups, state):
    with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:

        # پیام‌های جدید از ربات برق‌من
        source_entity = client.get_entity(SOURCE_BOT)
        last_id = state.get("last_source_id", 0)
        new_last_id = last_id
        new_pending = []

        for msg in client.iter_messages(source_entity, min_id=last_id, reverse=True):
            if msg.id > new_last_id:
                new_last_id = msg.id
            if not msg.text:
                continue
            if OUTAGE_PATTERN.search(msg.text):
                new_pending.append(msg.text)

        state["last_source_id"] = new_last_id

        # بررسی /activate در گروه‌ها
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
                    client.send_message(dialog.entity, "✅ این گروه با موفقیت برای دریافت اطلاع‌رسانی خاموشی برق فعال شد.")

            last_group_ids[chat_id] = newest_id

        state["last_group_ids"] = last_group_ids

    return new_pending


# ---------------------------------------------------------------------------
# بخش ۲: ارسال واقعی به گروه‌ها (با اکانت شخصی، چون ربات مدیریت عضو گروه‌ها نیست)
# ---------------------------------------------------------------------------
def dispatch_to_groups(text, target_chat_ids):
    with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        for chat_id in target_chat_ids:
            try:
                client.send_message(int(chat_id), text)
            except Exception as e:
                print(f"خطا در ارسال به {chat_id}: {e}")


def resolve_targets(groups, settings):
    default = settings.get("default_group_ids")
    if default:
        return [g for g in default if g in groups]
    return list(groups.keys())  # اگه پیش‌فرض تعریف نشده، همه‌ی گروه‌ها


# ---------------------------------------------------------------------------
# بخش ۳: ساخت پیام‌های در انتظار تایید و ارسال پیش‌نمایش به ادمین
# ---------------------------------------------------------------------------
def create_pending_entries(new_texts, pending, groups, settings):
    for text in new_texts:
        pid = str(int(time.time() * 1000)) + f"-{len(pending)}"
        pending[pid] = {
            "text": text,
            "created_at": time.time(),
            "awaiting_edit": False,
        }
        preview = (
            f"📨 پیام جدید خاموشی دریافت شد:\n\n{text}\n\n"
            f"مقصد فعلی: {len(resolve_targets(groups, settings))} گروه\n"
            f"⏱ اگه ظرف {settings.get('timeout_minutes', 15)} دقیقه پاسخ ندید"
            + (" و ارسال خودکار روشنه، خودکار ارسال می‌شه." if settings.get("autosend") else "، ارسال *نمی‌شه* (ارسال خودکار خاموشه).")
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ ارسال", "callback_data": f"send:{pid}"},
                {"text": "✏️ ویرایش", "callback_data": f"edit:{pid}"},
                {"text": "❌ لغو", "callback_data": f"cancel:{pid}"},
            ]]
        }
        send_to_admin(preview, reply_markup=json.dumps(keyboard))


# ---------------------------------------------------------------------------
# بخش ۴: بررسی timeout ها برای ارسال خودکار
# ---------------------------------------------------------------------------
def check_timeouts(pending, groups, settings, history):
    if not settings.get("autosend"):
        return
    timeout_seconds = settings.get("timeout_minutes", 15) * 60
    now = time.time()
    to_remove = []
    for pid, entry in pending.items():
        if entry.get("awaiting_edit"):
            continue
        if now - entry["created_at"] >= timeout_seconds:
            targets = resolve_targets(groups, settings)
            dispatch_to_groups(entry["text"], targets)
            history.append({"text": entry["text"], "sent_at": now, "mode": "auto-timeout", "targets": targets})
            send_to_admin(f"⏱ زمان تمام شد، پیام خودکار ارسال شد به {len(targets)} گروه.")
            to_remove.append(pid)
    for pid in to_remove:
        del pending[pid]


# ---------------------------------------------------------------------------
# بخش ۵: پردازش دستورات و دکمه‌های ربات مدیریت
# ---------------------------------------------------------------------------
def handle_manager_updates(groups, settings, pending, history):
    offset_data = load_json(MGR_OFFSET_FILE, {"offset": 0})
    resp = requests.get(f"{MANAGER_API}/getUpdates", params={"offset": offset_data["offset"], "timeout": 0}, timeout=15)
    data = resp.json()
    if not data.get("ok"):
        print("getUpdates failed:", data)
        return

    for update in data.get("result", []):
        offset_data["offset"] = update["update_id"] + 1

        # ---- دکمه‌ها ----
        cq = update.get("callback_query")
        if cq:
            user_id = cq["from"]["id"]
            if user_id != ADMIN_ID:
                continue
            data_str = cq["data"]
            action, pid = data_str.split(":", 1)
            entry = pending.get(pid)
            mgr_call("answerCallbackQuery", callback_query_id=cq["id"])

            if not entry:
                mgr_call("sendMessage", chat_id=ADMIN_ID, text="این پیام قبلاً پردازش شده.")
                continue

            if action == "send":
                targets = resolve_targets(groups, settings)
                dispatch_to_groups(entry["text"], targets)
                history.append({"text": entry["text"], "sent_at": time.time(), "mode": "manual", "targets": targets})
                send_to_admin(f"✅ ارسال شد به {len(targets)} گروه.")
                del pending[pid]

            elif action == "cancel":
                send_to_admin("❌ لغو شد، ارسال نمی‌شود.")
                del pending[pid]

            elif action == "edit":
                entry["awaiting_edit"] = True
                send_to_admin("متن جدید را برایم بفرستید (کل متن جایگزین می‌شود):")

            continue

        # ---- پیام‌های متنی ----
        msg = update.get("message")
        if not msg:
            continue
        if msg.get("from", {}).get("id") != ADMIN_ID:
            continue
        text = (msg.get("text") or "").strip()
        if not text:
            continue

        # آیا در حال ویرایش یکی از pending هاست؟
        editing_pid = next((pid for pid, e in pending.items() if e.get("awaiting_edit")), None)
        if editing_pid:
            pending[editing_pid]["text"] = text
            pending[editing_pid]["awaiting_edit"] = False
            pending[editing_pid]["created_at"] = time.time()  # تایمر از نو شروع می‌شود
            keyboard = {
                "inline_keyboard": [[
                    {"text": "✅ ارسال", "callback_data": f"send:{editing_pid}"},
                    {"text": "✏️ ویرایش دوباره", "callback_data": f"edit:{editing_pid}"},
                    {"text": "❌ لغو", "callback_data": f"cancel:{editing_pid}"},
                ]]
            }
            send_to_admin(f"متن ویرایش شد:\n\n{text}", reply_markup=json.dumps(keyboard))
            continue

        # ---- دستورات ----
        if text == "/groups":
            send_to_admin(group_list_text(groups))

        elif text.startswith("/setdefault"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_to_admin("فرمت درست: /setdefault 1,2  (شماره گروه‌ها طبق خروجی /groups، با کاما جدا؛ برای پاک‌کردن پیش‌فرض و ارسال به همه: /setdefault all)")
            elif parts[1].strip() == "all":
                settings["default_group_ids"] = None
                send_to_admin("پیش‌فرض پاک شد؛ از این به بعد به همه‌ی گروه‌ها ارسال می‌شود.")
            else:
                try:
                    indices = [int(x.strip()) for x in parts[1].split(",")]
                    chat_ids = list(groups.keys())
                    selected = [chat_ids[i - 1] for i in indices]
                    settings["default_group_ids"] = selected
                    titles = [groups[c] for c in selected]
                    send_to_admin("از این به بعد فقط به این گروه‌ها ارسال می‌شود:\n" + "\n".join(titles))
                except Exception as e:
                    send_to_admin(f"خطا در فرمت دستور: {e}")

        elif text.startswith("/autosend"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or parts[1] not in ("on", "off"):
                send_to_admin("فرمت درست: /autosend on  یا  /autosend off")
            else:
                settings["autosend"] = (parts[1] == "on")
                send_to_admin(f"ارسال خودکار {'روشن' if settings['autosend'] else 'خاموش'} شد.")

        elif text.startswith("/timeout"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip().isdigit():
                send_to_admin("فرمت درست: /timeout 15   (به دقیقه)")
            else:
                settings["timeout_minutes"] = int(parts[1].strip())
                send_to_admin(f"زمان انتظار روی {settings['timeout_minutes']} دقیقه تنظیم شد.")

        elif text == "/status":
            targets = resolve_targets(groups, settings)
            send_to_admin(
                f"ارسال خودکار: {'روشن' if settings.get('autosend') else 'خاموش'}\n"
                f"زمان انتظار: {settings.get('timeout_minutes', 15)} دقیقه\n"
                f"گروه‌های مقصد فعلی: {len(targets)} از {len(groups)}\n"
                f"پیام‌های در انتظار: {len(pending)}"
            )

        elif text == "/history":
            if not history:
                send_to_admin("هنوز پیامی ارسال نشده.")
            else:
                lines = []
                for h in history[-10:]:
                    t = time.strftime("%Y-%m-%d %H:%M", time.localtime(h["sent_at"]))
                    mode = "خودکار" if h["mode"] == "auto-timeout" else "دستی"
                    lines.append(f"[{t}] ({mode}) {h['text'][:50]}...")
                send_to_admin("۱۰ پیام آخر:\n\n" + "\n\n".join(lines))

        elif text == "/help" or text == "/start":
            send_to_admin(
                "دستورات:\n"
                "/groups - لیست گروه‌های ثبت‌شده\n"
                "/setdefault 1,2 - تعیین گروه‌های مقصد پیش‌فرض\n"
                "/autosend on|off - روشن/خاموش کردن ارسال خودکار\n"
                "/timeout 15 - تعیین دقیقه‌ی انتظار قبل از ارسال خودکار\n"
                "/status - وضعیت فعلی\n"
                "/history - پیام‌های اخیر ارسال‌شده"
            )

    save_json(MGR_OFFSET_FILE, offset_data)


def main():
    groups = load_json(GROUPS_FILE, {})
    state = load_json(STATE_FILE, {"last_source_id": 0, "last_group_ids": {}})
    settings = load_json(SETTINGS_FILE, {"autosend": False, "timeout_minutes": 15, "default_group_ids": None})
    pending = load_json(PENDING_FILE, {})
    history = load_json(HISTORY_FILE, [])

    # ۱. دستورات و دکمه‌های ادمین را اول پردازش کن (پاسخ سریع‌تر)
    handle_manager_updates(groups, settings, pending, history)

    # ۲. پیام‌های جدید منبع + ثبت گروه با کد فعال‌سازی
    new_texts = poll_source_and_groups(groups, state)
    if new_texts:
        create_pending_entries(new_texts, pending, groups, settings)

    # ۳. بررسی timeout برای ارسال خودکار
    check_timeouts(pending, groups, settings, history)

    save_json(GROUPS_FILE, groups)
    save_json(STATE_FILE, state)
    save_json(SETTINGS_FILE, settings)
    save_json(PENDING_FILE, pending)
    save_json(HISTORY_FILE, history)


if __name__ == "__main__":
    main()
