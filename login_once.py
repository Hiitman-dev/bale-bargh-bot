"""
login_once.py
----------------
این اسکریپت را فقط "یک‌بار" و روی کامپیوتر خودتان (نه GitHub Actions) اجرا کنید.
از شما شماره موبایل و کد تاییدی که تلگرام برایتان پیامک/پیام می‌کند می‌پرسد،
و در پایان یک "session string" چاپ می‌کند.

این رشته را کپی کنید و به‌عنوان GitHub Secret به نام TELEGRAM_SESSION ذخیره کنید.
بعد از آن دیگر هرگز نیازی به اجرای این فایل یا لاگین مجدد نیست.

نصب پیش‌نیاز:
    pip install telethon
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(input("API_ID را وارد کنید: ").strip())
API_HASH = input("API_HASH را وارد کنید: ").strip()

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("\n✅ لاگین موفق بود.\n")
    print("این رشته را کپی کنید و به عنوان GitHub Secret با نام TELEGRAM_SESSION ذخیره کنید:\n")
    print(client.session.save())
    print("\n⚠️ این رشته معادل رمز عبور اکانت شماست، آن را جایی عمومی (چت، فایل شخصی ناامن) قرار ندهید.")
