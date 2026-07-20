# ربات مدیریت اطلاع‌رسانی خاموشی برق (نسخه‌ی گیت‌هاب + کلادفلر)

## منبع پیام
منبع پیام‌های خاموشی، ربات تلگرامی `@Bargheman1_bot` است (بله دیگر استفاده نمی‌شود). اگر
لازم شد بعداً ربات دیگری جایگزینش شود، فقط کافیست مقدار پیش‌فرض `SOURCE_BOT` در
`reader.py` را عوض کنید یا Secret اختیاری `SOURCE_BOT_USERNAME` را در ریپو تعریف کنید.

## معماری
- **خواندن پیام (`reader.py`)** — روی GitHub Actions، هر ۵ دقیقه اجرا می‌شود. با اکانت شخصی
  (Telethon) پیام‌های ربات منبع را می‌خواند (چون یک ربات نمی‌تواند مستقیم با ربات دیگری،
  یعنی `@Bargheman1_bot`، صحبت کند)، تاریخ/ساعت را استخراج می‌کند، در قالب درشت می‌سازد و
  به Cloudflare Worker می‌فرستد.
- **ربات مدیریت (`worker/`)** — روی Cloudflare Workers، به‌صورت Webhook. حالا خودش یک عضو
  واقعی گروه‌هاست: پیش‌نمایش با دکمه برای شما می‌فرستد، دستورات را مدیریت می‌کند، با Cron
  Trigger هر ۲ دقیقه تایم‌اوت‌ها را چک می‌کند، و وقتی تایید کنید یا تایم‌اوت بخورد، **خودش
  مستقیم** با Bot API به گروه‌ها پیام می‌فرستد — دیگر نیازی به گیت‌هاب یا اکانت شخصی برای
  ارسال نیست. فعال‌سازی گروه‌ها (`/activate <code>`) هم حالا مستقیم توسط خود ربات مدیریت
  (وقتی عضو گروه باشد) تشخیص داده می‌شود.
- `GITHUB_TOKEN`/`GITHUB_REPO` روی Worker فقط برای قابلیت `/setsecret` (پایین‌تر) لازمند،
  دیگر برای ارسال پیام استفاده نمی‌شوند.

## آماده‌سازی ربات مدیریت برای استفاده در گروه‌ها
۱. توی تلگرام، ربات مدیریتت رو (با همون یوزرنیمی که از BotFather ساختی) به هر گروه خانوادگی
   که می‌خوای پیام‌ها توش بره اضافه کن.
۲. اگه گروه محدودیت داره که فقط ادمین‌ها بتونن پیام بفرستن، ربات رو **ادمین** کن (حداقل با
   دسترسی «ارسال پیام»)، وگرنه لازم نیست.
۳. Secret جدید `ACTIVATION_CODE` رو روی خود Cloudflare هم بساز (دقیقاً همون مقداری که توی
   GitHub Secrets گذاشتی):
   ```bash
   npx wrangler secret put ACTIVATION_CODE
   ```
   (این جدا از نسخه‌ی گیت‌هابشه، چون تشخیص `/activate` حالا روی Worker انجام می‌شه، نه
   `reader.py`.)
۴. `npx wrangler deploy` رو دوباره بزن تا کد جدید بره بالا.
۵. توی هر گروه بنویس: `/activate <ACTIVATION_CODE>` — ربات مدیریت خودش جواب می‌ده و گروه رو
   فعال می‌کنه.

⚠️ **نکته‌ی مهم درباره‌ی `/setsecret ACTIVATION_CODE`:** این دستور فقط نسخه‌ی گیت‌هابیِ
سکرت رو عوض می‌کنه. چون تشخیص `/activate` در گروه‌ها الان روی Cloudflare انجام می‌شه، اگه
از `/setsecret` برای عوض کردن `ACTIVATION_CODE` استفاده کنی، باید دستی هم
`npx wrangler secret put ACTIVATION_CODE` رو با همون مقدار جدید بزنی — وگرنه دو نسخه با
هم فرق می‌کنن و فعال‌سازی گروه شکست می‌خوره.

## ۱. آماده‌سازی گیت‌هاب

### Secrets ریپو (Settings → Secrets and variables → Actions)
| Name | مقدار |
|---|---|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | از my.telegram.org |
| `TELEGRAM_SESSION` | خروجی `python login_once.py` (یک‌بار، روی کامپیوتر خودتان) |
| `ACTIVATION_CODE` | یک کد دلخواه و خصوصی برای فعال‌سازی گروه‌ها |
| `WORKER_BASE_URL` | آدرس Worker بعد از دیپلوی، مثلا `https://bargh-manager.YOURSUBDOMAIN.workers.dev` |
| `WORKER_SHARED_SECRET` | یک رشته‌ی تصادفی دلخواه — باید دقیقاً با `READER_SECRET` روی Worker یکی باشد |
| `STATUS_QUERY_TIMES` | اختیاری. ساعت‌هایی که خودش `/status` رو به ربات منبع بفرسته، مثلا `07:00,13:00,20:00` (به وقت تهران، جدا شده با کاما) |

### درباره‌ی STATUS_QUERY_TIMES
اگه این Secret رو تنظیم کنی، `reader.py` سر هر کدوم از این ساعت‌ها (با دقت حدود ۵ دقیقه، چون
هر ۵ دقیقه اجرا می‌شه) خودش دستور `/status` رو به `@Bargheman1_bot` می‌فرسته، ۵ ثانیه صبر
می‌کنه، و اگه جواب ربات شامل یک تاریخ/ساعت خاموشی بود، همون رو با قالب درشت به Worker
می‌فرسته (دقیقاً مثل پیام‌های عادی، با پیش‌نمایش و دکمه). اگه جواب ربات خبری از خاموشی نداشت،
هیچی فوروارد نمی‌شه. هر ساعت فقط یک‌بار در روز چک می‌شه (رکوردش توی `state.json` نگه داشته می‌شه).

### فعال‌سازی گروه‌ها (روش قدیمی، اختیاری)
`reader.py` هنوز هم می‌تونه `/activate <ACTIVATION_CODE>` رو از طریق اکانت شخصی (اگه عضو
گروه باشه) تشخیص بده — ولی از این به بعد روش اصلی همونیه که پایین‌تر، بخش «آماده‌سازی ربات
مدیریت برای استفاده در گروه‌ها» توضیح داده شده (خود ربات مدیریت مستقیم توی گروهه). نیازی
نیست هر دو رو انجام بدی؛ اگه ربات مدیریت رو به همه‌ی گروه‌ها اضافه کردی، این روش قدیمی رو
می‌تونی نادیده بگیری.

## ۲. دیپلوی Cloudflare Worker

```bash
cd worker
npm install
npx wrangler kv namespace create BARGH_KV
# آی‌دی خروجی رو توی wrangler.toml جای REPLACE_WITH_YOUR_KV_NAMESPACE_ID بذارید

npx wrangler secret put MANAGER_BOT_TOKEN     # توکن ربات مدیریت از BotFather
npx wrangler secret put ADMIN_USER_ID         # آیدی عددی شما از userinfobot
npx wrangler secret put READER_SECRET         # همون مقدار WORKER_SHARED_SECRET بالا
npx wrangler secret put ACTIVATION_CODE       # همون مقدار ACTIVATION_CODE توی گیت‌هاب
npx wrangler secret put GITHUB_TOKEN          # فقط برای /setsecret لازمه (پایین‌تر توضیح داده شده)
npx wrangler secret put GITHUB_REPO           # مثلا yourusername/bargh-manager

npx wrangler deploy
```

بعد از دیپلوی، وب‌هوک تلگرام را روی آدرس Worker تنظیم کنید:

```bash
curl "https://api.telegram.org/bot<MANAGER_BOT_TOKEN>/setWebhook?url=https://bargh-manager.YOURSUBDOMAIN.workers.dev/webhook"
```

با ربات مدیریت `/start` بزنید (تلگرام اجازه نمی‌دهد ربات‌ها اول پیام بدهند).

## تغییر سکرت‌ها از تلگرام (`/setsecret`)
با فرستادن `/setsecret NAME value` به ربات مدیریت (مثلاً `/setsecret ACTIVATION_CODE abc123`)
می‌تونی سکرت‌های گیت‌هاب رو مستقیم از تلگرام عوض کنی — دیگه لازم نیست بری ترمینال.
عمداً فقط این دوتا سکرت مجازن (توی `EDITABLE_SECRETS` در `worker/src/index.js`):
`STATUS_QUERY_TIMES`, `ACTIVATION_CODE`.

برای این‌که کار کنه، به `GITHUB_TOKEN` باید یه دسترسی اضافه بدی: برو همون Fine-grained
Token که ساختی → `Permissions` → `Secrets: Read and write` رو هم اضافه کن (کنار
`Contents` و `Actions` قبلی). بعد دوباره `npx wrangler secret put GITHUB_TOKEN` رو با
همون توکن (یا یه توکن جدید با همون دسترسی‌ها) اجرا کن.

⚠️ مقداری که با `/setsecret` می‌فرستی، به‌صورت پیام معمولی توی چتت با ربات ذخیره می‌مونه.
برای همین این قابلیت عمداً به دو سکرت کم‌ریسک بالا محدود شده؛ چیزی مثل `TELEGRAM_SESSION`
هرگز نباید از این مسیر عوض بشه — همیشه دستی و از ترمینال.

## نکات مهم
- `GITHUB_TOKEN` باید Fine-grained PAT با دسترسی نوشتن روی همین ریپو (Actions/Contents) باشد.
- Worker روی پلن رایگان کلادفلر هم کار می‌کند؛ اگر تعداد پیام‌ها/گروه‌ها خیلی زیاد شد،
  محدودیت CPU هر Cron (۱۰ میلی‌ثانیه در پلن رایگان) ممکن است تنگ باشد — در آن صورت پلن
  Paid ($5/ماه) پیشنهاد می‌شود.
- ریپوی گیت‌هاب را Private نگه دارید؛ `TELEGRAM_SESSION` معادل رمز اکانت شخصی شماست.
