# ربات مدیریت اطلاع‌رسانی خاموشی برق (نسخه‌ی گیت‌هاب + کلادفلر)

## منبع پیام
منبع پیام‌های خاموشی، ربات تلگرامی `@Bargheman1_bot` است (بله دیگر استفاده نمی‌شود). اگر
لازم شد بعداً ربات دیگری جایگزینش شود، فقط کافیست مقدار پیش‌فرض `SOURCE_BOT` در
`reader.py` را عوض کنید یا Secret اختیاری `SOURCE_BOT_USERNAME` را در ریپو تعریف کنید.

## معماری
- **خواندن پیام (`reader.py`)** — روی GitHub Actions، هر ۵ دقیقه اجرا می‌شود. با اکانت شخصی
  (Telethon) پیام‌های ربات منبع را می‌خواند، تاریخ/ساعت را استخراج می‌کند، در قالب درشت
  می‌سازد و به Cloudflare Worker می‌فرستد. فعال‌سازی گروه‌ها (`/activate`) هم همین‌جا انجام می‌شود.
- **ربات مدیریت (`worker/`)** — روی Cloudflare Workers، به‌صورت Webhook. پیش‌نمایش با دکمه برای
  شما می‌فرستد، دستورات `/groups /setdefault /autosend /timeout /status /history` را مدیریت
  می‌کند، و با Cron Trigger هر ۲ دقیقه تایم‌اوت‌ها را چک می‌کند.
- **ارسال نهایی (`send.py`)** — چون ربات مدیریت عضو گروه‌ها نیست، وقتی شما تایید کنید یا
  تایم‌اوت بخورد، Worker یک رویداد `repository_dispatch` به گیت‌هاب می‌فرستد و این اسکریپت با
  اکانت شخصی پیام را واقعاً به گروه‌ها می‌فرستد.

## ۱. آماده‌سازی گیت‌هاب

### Secrets ریپو (Settings → Secrets and variables → Actions)
| Name | مقدار |
|---|---|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | از my.telegram.org |
| `TELEGRAM_SESSION` | خروجی `python login_once.py` (یک‌بار، روی کامپیوتر خودتان) |
| `ACTIVATION_CODE` | یک کد دلخواه و خصوصی برای فعال‌سازی گروه‌ها |
| `WORKER_BASE_URL` | آدرس Worker بعد از دیپلوی، مثلا `https://bargh-manager.YOURSUBDOMAIN.workers.dev` |
| `WORKER_SHARED_SECRET` | یک رشته‌ی تصادفی دلخواه — باید دقیقاً با `READER_SECRET` روی Worker یکی باشد |

### فعال‌سازی گروه‌ها
در هر گروه، با اکانت شخصی‌تان بنویسید: `/activate <ACTIVATION_CODE>`

## ۲. دیپلوی Cloudflare Worker

```bash
cd worker
npm install
npx wrangler kv namespace create BARGH_KV
# آی‌دی خروجی رو توی wrangler.toml جای REPLACE_WITH_YOUR_KV_NAMESPACE_ID بذارید

npx wrangler secret put MANAGER_BOT_TOKEN     # توکن ربات مدیریت از BotFather
npx wrangler secret put ADMIN_USER_ID         # آیدی عددی شما از userinfobot
npx wrangler secret put READER_SECRET         # همون مقدار WORKER_SHARED_SECRET بالا
npx wrangler secret put GITHUB_TOKEN          # PAT با اسکوپ repo، فقط برای این ریپو
npx wrangler secret put GITHUB_REPO           # مثلا yourusername/bargh-manager

npx wrangler deploy
```

بعد از دیپلوی، وب‌هوک تلگرام را روی آدرس Worker تنظیم کنید:

```bash
curl "https://api.telegram.org/bot<MANAGER_BOT_TOKEN>/setWebhook?url=https://bargh-manager.YOURSUBDOMAIN.workers.dev/webhook"
```

با ربات مدیریت `/start` بزنید (تلگرام اجازه نمی‌دهد ربات‌ها اول پیام بدهند).

## نکات مهم
- `GITHUB_TOKEN` باید Fine-grained PAT با دسترسی نوشتن روی همین ریپو (Actions/Contents) باشد.
- Worker روی پلن رایگان کلادفلر هم کار می‌کند؛ اگر تعداد پیام‌ها/گروه‌ها خیلی زیاد شد،
  محدودیت CPU هر Cron (۱۰ میلی‌ثانیه در پلن رایگان) ممکن است تنگ باشد — در آن صورت پلن
  Paid ($5/ماه) پیشنهاد می‌شود.
- ریپوی گیت‌هاب را Private نگه دارید؛ `TELEGRAM_SESSION` معادل رمز اکانت شخصی شماست.
