# API ربات — Bot API

این API برای وصل کردن یک ربات فروش تلگرام (مال خودتان یا کس دیگری) به پنل است: ثبت‌نام مشتری با آیدی تلگرام، دیدن حساب، ثبت آی‌پی، دیدن پلن‌ها، فرستادن رسید و تیکت پشتیبانی. تأیید رسید مثل همیشه با شماست، در صفحهٔ رسیدهای پنل ادمین؛ با تأیید، پلن خودکار روی حساب مشتری می‌نشیند و ربات در درخواست بعدی‌اش آن را می‌بیند.

*A door for a Telegram sales bot: open a customer by Telegram id, read their account, register their address, list plans, send a receipt, open and answer support tickets. Approving receipts stays in the admin panel. JSON over HTTPS, one key per bot.*

## راه‌اندازی

1. پنل ادمین → **API** → «کلید تازه». یک اسم بدهید و اگر آی‌پی سرور ربات ثابت است، آن را هم بنویسید تا کلید از جای دیگری کار نکند.
2. کلید (با `dd_` شروع می‌شود) **فقط همان یک بار** نشان داده می‌شود. آن را در تنظیمات ربات بگذارید.
3. آدرس API روی همان صفحه نوشته شده: `https://<دامنهٔ پنل>:8445/api/v1/`. اگر فایروال سرور یا دیتاسنتر دارید، پورت **8445** را باز کنید.

کلید به جای **همهٔ** مشتری‌ها کار می‌کند — ربات می‌گوید کدام مشتری، با آیدی تلگرامش — پس مثل رمز پنل از آن نگهداری کنید. اگر لو رفت، همان صفحه → «باطل کردن»؛ فوراً از کار می‌افتد.

گواهی همان گواهی پنل ادمین است (Let's Encrypt)، پس ربات می‌تواند مثل هر سایت دیگری گواهی را چک کند، و لازم نیست چک را خاموش کند.

## قواعد کلی

- هر درخواست سرآیند `Authorization: Bearer dd_...` دارد.
- بدنه‌ها JSON است، با `Content-Type: application/json`.
- هر جواب `ok` دارد. جواب ناموفق `error` (کدی برای ربات) و `message` (متن فارسی که می‌شود عیناً به مشتری نشان داد) هم دارد:

```json
{"ok": false, "error": "ip_taken", "message": "این آی‌پی به حساب دیگری ثبت شده است"}
```

- سقف: ۳۰۰ درخواست در دقیقه برای هر کلید. بیشتر از آن `429` با سرآیند `Retry-After` می‌گیرد. ۲۰ کلید اشتباه از یک آدرس در ۵ دقیقه هم همین‌طور.
- حجم‌ها به بایت است و قیمت‌ها به تومان. `quota_bytes: 0` یعنی نامحدود — ولی فقط وقتی `status` برابر `active` است؛ حساب `pending` هم حجمش صفر است و هیچ چیز نمی‌گیرد. **همیشه اول `status` را نگاه کنید.**
- زمان‌ها ISO 8601 و UTC هستند، مثل `2026-10-21T17:52:55+00:00`.

### درخواست تکراری — `Idempotency-Key`

به هر `POST` می‌شود سرآیند `Idempotency-Key` داد (حداکثر ۱۰۰ نویسه، مثلاً `receipt-<telegram_id>-<message_id>`). اگر همان درخواست با همان کلید دوباره برسد — ربات بعد از قطعی دوباره فرستاده، یا مشتری دو بار دکمه را زده — **همان جواب اول** برمی‌گردد (با سرآیند `Idempotent-Replayed: true`) و کار دوباره انجام نمی‌شود. همان کلید برای درخواستی متفاوت `422` می‌گیرد. جواب‌ها ۷ روز نگه داشته می‌شوند.

برای رسیدها حتماً از آن استفاده کنید.

## دستورها

در مثال‌ها:

```sh
API=https://panel.example.com:8445/api/v1
KEY=dd_...
```

### `GET /` — امتحان کلید

```sh
curl -s -H "Authorization: Bearer $KEY" $API/
```
```json
{"ok": true, "version": "0.6.0"}
```

### `GET /plans` — پلن‌های در حال فروش

```sh
curl -s -H "Authorization: Bearer $KEY" $API/plans
```
```json
{"ok": true, "plans": [
  {"id": 1, "name": "گیمینگ ماهانه", "template": "بازی", "days": 30,
   "quota_bytes": 107374182400, "price": 200000, "speed_kbps": 0,
   "note": "فقط بازی‌ها و پلی‌استیشن",
   "games": ["والورانت", "فورتنایت", "کال آو دیوتی", "استیم"]},
  {"id": 2, "name": "کامل ماهانه", "template": "کامل", "days": 30,
   "quota_bytes": 0, "price": 500000, "speed_kbps": 0, "note": null,
   "games": ["والورانت", "فورتنایت", "..."]}
]}
```

به ترتیب قالب و بعد ارزان به گران. `quota_bytes: 0` یعنی حجم نامحدود. `speed_kbps: 0` یعنی بدون سقف سرعت.

`games` اسم بازی‌هایی است که آن پلن پوشش می‌دهد — همان چیزی که مشتری می‌فهمد، به‌جای اسم سرویس‌ها. یک بازی وقتی در این فهرست می‌آید که **همهٔ** سرویس‌های لازمش در آن قالب روشن باشند (نیمِ والورانت، والورانت نیست). فهرست می‌تواند بلند باشد؛ در ربات چند تای اول را نشان بدهید و بقیه را بشمارید. اگر پنل قدیمی باشد این فیلد نیست، پس با `plan.get("games", [])` بخوانیدش.

### `POST /users` — ثبت‌نام (یا پیدا کردن) مشتری

```sh
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"telegram_id": 123456789, "name": "علی"}' $API/users
```

مشتری تازه `201` می‌گیرد، و اگر با این آیدی از قبل هست `200` — همان حساب، نه یک حساب دوم. پس لازم نیست ربات اول بپرسد هست یا نه؛ در `/start` همین را صدا بزنید.

```json
{"ok": true, "created": true, "user": { ... }}
```

حساب تازه `pending` است و تا وقتی پلنی رویش ننشیند سرویس نمی‌گیرد.

### `GET /users/{telegram_id}` — حساب مشتری

```sh
curl -s -H "Authorization: Bearer $KEY" $API/users/123456789
```
```json
{"ok": true, "user": {
  "id": 7, "telegram_id": 123456789, "name": "علی", "username": "ali_gamer",
  "panel_url": "https://user.example.com:8443/",
  "status": "active",
  "plan": {"id": 1, "name": "گیمینگ ماهانه"}, "template": "بازی",
  "unlimited": false, "quota_bytes": 107374182400, "used_bytes": 5368709120,
  "remaining_bytes": 102005473280, "speed_kbps": 0,
  "expires_at": "2026-10-21T17:52:55+00:00",
  "ips": ["5.120.1.2"], "max_ips": 1, "wallet": 0,
  "dns": ["203.0.113.4"],
  "tickets_answered": 0,
  "trial": null,
  "receipt_waiting": null
}}
```

| `status` | یعنی |
|---|---|
| `pending` | ثبت‌نام کرده، هنوز پلنی ندارد |
| `active` | سرویس دارد |
| `over_quota` | حجمش تمام شده |
| `expired` | مدتش تمام شده |
| `suspended` | ادمین مسدودش کرده |

- `dns` همان آدرسی است که مشتری باید در کنسول یا مودم به‌عنوان DNS بگذارد (آدرس رله‌ها).
- `receipt_waiting` اگر رسیدی منتظر تأیید باشد: `{"id", "created_at", "amount", "plan"}`.
- مشتری ناشناس: `404` با `user_not_found`.

### `POST /users/{telegram_id}/ips` — ثبت آی‌پی

```sh
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"ip": "5.120.1.2"}' $API/users/123456789/ips
```

سرویس فقط روی آی‌پی ثبت‌شده کار می‌کند. هر حساب `max_ips` آی‌پی دارد (معمولاً ۱)؛ آی‌پی تازه جای قدیمی‌ترین را می‌گیرد، پس «آی‌پی‌ام عوض شد» همین یک درخواست است.

- آی‌پی عمومی لازم است؛ `192.168.x.x` و مثل آن `400` با `bad_ip` می‌گیرد.
- آی‌پی‌ای که مال حساب دیگری است: `409` با `ip_taken`.

ربات آی‌پی مشتری را خودش نمی‌بیند؛ مشتری باید بگوید (از صفحهٔ مودم یا یک سایت «آی‌پی من چیست» روی همان اینترنت). راه ساده‌تر برای مشتری، پنل وب رله است که آی‌پی را خودش تشخیص می‌دهد.

### `DELETE /users/{telegram_id}/ips/{ip}` — حذف آی‌پی

```sh
curl -s -X DELETE -H "Authorization: Bearer $KEY" $API/users/123456789/ips/5.120.1.2
```

آی‌پی‌ای که روی این حساب نیست: `404` با `ip_not_found`.

### `POST /users/{telegram_id}/receipts` — فرستادن رسید

```sh
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -H "Idempotency-Key: receipt-123456789-4521" \
     -d "{\"plan_id\": 1, \"content_type\": \"image/jpeg\", \"data\": \"$(base64 -w0 slip.jpg)\"}" \
     $API/users/123456789/receipts
```

| فیلد | |
|---|---|
| `plan_id` | پلنی که خریده. تا وقتی پلنی در حال فروش است لازم است (`plan_required`)؛ پلن خاموش یا حذف‌شده `409` با `plan_not_on_sale` |
| `content_type` | `image/jpeg`، `image/png`، `image/webp` یا `application/pdf` |
| `data` | خود فایل، base64 — حداکثر ۴ مگابایت |
| `note` | اختیاری، تا ۲۰۰ نویسه |

```json
{"ok": true, "message": "رسید فرستاده شد. پس از بررسی حسابتان شارژ می‌شود",
 "receipt": {"id": 31, "amount": 200000, "status": "pending",
             "plan": {"id": 1, "name": "گیمینگ ماهانه"},
             "created_at": "2026-09-21T18:03:10+00:00", "decided_at": null}}
```

- مبلغ از خود پلن می‌آید، نه از درخواست.
- هر مشتری یک رسید در انتظار دارد؛ رسید تازه جای قبلی را می‌گیرد.
- با تأیید ادمین: خرید دوبارهٔ **همان** پلن پیش از تمام شدنش تمدید است (روزها و حجم روی باقی‌مانده اضافه می‌شوند). پلن **دیگر** از لحظهٔ تأیید از نو شروع می‌شود و باقی‌ماندهٔ قبلی از بین می‌رود — این را پیش از خرید به مشتری بگویید.
- فایل تلگرام را ربات با `getFile` می‌گیرد و base64 می‌کند.

### `GET /users/{telegram_id}/receipts` — رسیدهای مشتری

```sh
curl -s -H "Authorization: Bearer $KEY" $API/users/123456789/receipts
```

بیست رسید آخر، تازه‌ترین اول. `status` هر کدام `pending`، `approved` یا `rejected` است.

### ورود به پنل وب برای مشتری‌های ربات

مشتری‌ای که از ربات آمده، با این سه دستور پنل وب هم دارد:

| | |
|---|---|
| `POST /users/{telegram_id}/credentials` | `{"username": "ali_gamer", "name": "علی"}` — نام کاربری و اسم را می‌گذارد و یک رمز تصادفی می‌سازد: `{"username", "password", "panel_url"}`. نام کاربری همان قاعدهٔ ثبت‌نام وب را دارد (۳ تا ۳۲ حرف انگلیسی، عدد، `.`، `-`، `_`)؛ بد: `400` با `bad_username`، تکراری: `409` با `username_taken`، حسابی که از قبل نام کاربری دارد: `409` با `has_username` |
| `POST /users/{telegram_id}/password` | رمز تصادفی تازه؛ هر جا با رمز قبلی وارد بود خارج می‌شود |
| `POST /users/{telegram_id}/login-link` | لینک یک‌بارمصرف ورود: `{"url", "minutes": 10}`. مشتری بازش می‌کند و بدون رمز وارد حسابش می‌شود — برای ثبت آی‌پی، لینک را با همان اینترنتی باز کند که سرویس را رویش می‌خواهد |

`panel_url` (در حساب مشتری هم هست) آدرس پنل مشتری است؛ سرور ایران خودش به پنل می‌گوید. تا وقتی سرور ایرانی همگام نشده، `null` است و `login-link` جواب `503` با `no_panel` می‌دهد.

لینک ورود را **بدون پیش‌نمایش** بفرستید (`link_preview_options: {"is_disabled": true}` در `sendMessage`). باز کردن آدرس لینک را مصرف نمی‌کند — صفحه خودش آن را برمی‌گرداند — ولی بهتر است تلگرام هم سراغش نرود.

### `POST /users/{telegram_id}/trial` — تست رایگان

اگر ادمین پلنی را با تیک «🎁 تست رایگان» ساخته باشد، مشتری با یک درخواست، بدون رسید، می‌گیردش:

```sh
curl -s -X POST -H "Authorization: Bearer $KEY" $API/users/123456789/trial
```
```json
{"ok": true, "message": "🎁 تست رایگان فعال شد. پلن «تست یک‌روزه» فعال شد تا 2026-09-23",
 "user": { ... }}
```

هر تلگرام و هر حساب فقط یک بار (حتی اگر حساب حذف یا تلگرامش جدا شود). کسی که همین حالا سرویس فعال دارد نمی‌گیردش تا باقی‌ماندهٔ پلنش از بین نرود. پلن تست در `GET /plans` نیست؛ در حساب مشتری، فیلد `trial` می‌گوید هست یا نه:

- `null`: تستی نیست، یا این مشتری قبلاً گرفته.
- `{"plan": {...}, "available": true, "why": null}`: می‌تواند بگیرد — دکمه‌اش را نشان دهید.
- `{"plan": {...}, "available": false, "why": "..."}`: الان نمی‌تواند؛ `why` متن فارسی دلیلش است.

خطاها: `404` با `trial_none`، `409` با `trial_used` یا `trial_running`.

### `POST /link` — وصل کردن حساب وب به تلگرام

مشتری‌ای که از پنل وب ثبت‌نام کرده آیدی تلگرام ندارد. در صفحهٔ حسابش «اتصال حساب به تلگرام»، با رمز فعلی حساب، یک کد هشت‌حرفی می‌دهد (۱۵ دقیقه اعتبار). مشتری همان‌جا می‌تواند تلگرام را جدا هم کند (مثلاً وقتی تلگرامش عوض شده)، و ادمین هم از صفحهٔ کاربران. مشتری کد را برای ربات می‌فرستد و ربات آن را به پنل می‌دهد:

```sh
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"telegram_id": 123456789, "code": "k7m2pq9x"}' $API/link
```
```json
{"ok": true, "message": "حساب شما به تلگرام وصل شد", "user": { ... }}
```

اگر در پنل ادمین (صفحهٔ API) «آدرس ربات برای مشتری‌ها» را بگذارید (مثلاً `https://t.me/MyBot`)، دکمهٔ پنل مشتری ربات را مستقیم باز می‌کند و ربات پیام `/start link_k7m2pq9x` می‌گیرد؛ کد همان بعد از `link_` است.

- کد اشتباه یا استفاده‌شده: `400` با `bad_code`؛ منقضی: `400` با `code_expired`.
- این تلگرام از قبل به حساب دیگری وصل است: `409` با `telegram_in_use`.

بعد از اتصال، اگر مشتری رمز پنل وب را فراموش کند، در صفحهٔ ورود «بازیابی با تلگرام» را می‌زند و پنل یک کد شش‌رقمی با خبر `password.reset_code` برای ربات می‌فرستد (پایین‌تر). ربات فقط باید آن را برای مشتری بفرستد.

### تیکت‌ها

مشتری از ربات هم می‌تواند تیکت بفرستد؛ همان تیکت در پنل وب مشتری و در صفحهٔ «تیکت‌ها»ی پنل ادمین دیده می‌شود، و جواب ادمین هر سه جا.

`status` هر تیکت می‌گوید نوبت کیست: `open` منتظر جواب ادمین، `answered` ادمین جواب داده، `closed` بسته. پیام تازهٔ مشتری در تیکت بسته دوباره بازش می‌کند. `tickets_answered` در حساب مشتری تعداد تیکت‌هایی است که جوابشان آمده — ربات می‌تواند با آن به مشتری خبر بدهد.

| | |
|---|---|
| `GET /users/{telegram_id}/tickets` | فهرست تیکت‌ها (پنجاه تای آخر)، اول آن‌هایی که جواب آمده |
| `POST /users/{telegram_id}/tickets` | تیکت تازه: `{"subject": "...", "body": "..."}` — جواب `201` با خود تیکت |
| `GET /users/{telegram_id}/tickets/{id}` | یک تیکت با همهٔ پیام‌هایش |
| `POST /users/{telegram_id}/tickets/{id}/messages` | پیام تازه: `{"body": "..."}` |
| `POST /users/{telegram_id}/tickets/{id}/close` | بستن |
| `GET /users/{telegram_id}/tickets/{id}/messages/{message_id}/image` | عکس یک پیام: `{"image_type", "image_data"}` (base64) |

هر پیام می‌تواند یک عکس داشته باشد: `"image_type": "image/jpeg"` (یا png، webp) و `"image_data"` به base64، حداکثر ۴ مگابایت.

```sh
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"subject": "بازی وصل نمی‌شود", "body": "از دیشب FC26 بالا نمی‌آید"}' \
     $API/users/123456789/tickets
```
```json
{"ok": true, "message": "تیکت ثبت شد؛ جواب همین‌جا می‌آید",
 "ticket": {"id": 12, "subject": "بازی وصل نمی‌شود", "status": "open",
            "created_at": "...", "updated_at": "...",
            "messages": [{"id": 40, "from": "customer", "body": "از دیشب FC26 بالا نمی‌آید",
                          "has_image": false, "created_at": "..."}]}}
```

`from` هر پیام `customer` یا `admin` است. محدودیت‌ها: موضوع ۸۰ نویسه، پیام ۲۰۰۰ نویسه، هر مشتری حداکثر ۳ تیکت باز (`409` با `too_many_tickets`) و ۲۰ پیام در ساعت (`429`). عکس‌های تیکت بسته ۳۰ روز بعد پاک می‌شوند؛ متن می‌ماند.

## خبر دادن پنل به ربات — webhook

به جای اینکه ربات مدام وضعیت همه را بپرسد، پنل خودش خبر می‌دهد. پنل ادمین → **API** → «خبر دادن به ربات»: آدرس ربات را جلوی کلیدش بنویسید و ذخیره کنید. یک **رمز امضا** (`whsec_...`) یک بار نشان داده می‌شود؛ آن را در ربات بگذارید. دکمهٔ «ارسال آزمایشی» یک خبر `ping` می‌فرستد و پایین همان صفحه دیده می‌شود رسید یا نه.

آدرس باید `https` باشد؛ `http` فقط وقتی ربات روی همان سرور است (`http://127.0.0.1:...`). گواهی ربات چک می‌شود، پس گواهی درست لازم است.

هر خبر یک `POST` با بدنهٔ JSON است:

```json
{"id": 57, "event": "receipt.approved", "created_at": "2026-09-22T10:14:03+00:00",
 "telegram_id": 123456789, "user_id": 7,
 "data": {"receipt_id": 31, "plan": {"id": 1, "name": "گیمینگ ماهانه"},
          "expires_at": "2026-10-22T10:14:03+00:00", "quota_bytes": 107374182400,
          "text": "رسید پرداخت شما تأیید شد. پلن «گیمینگ ماهانه» فعال شد تا 2026-10-22"}}
```

`data.text` یک متن فارسی آماده است؛ ربات ساده می‌تواند همان را برای `telegram_id` بفرستد و کار تمام است.

| `event` | کی | در `data` |
|---|---|---|
| `receipt.approved` | ادمین رسید را تأیید کرد | `receipt_id`، `plan`، `expires_at`، `quota_bytes` |
| `receipt.rejected` | ادمین رسید را رد کرد | `receipt_id` |
| `plan.activated` | ادمین دستی پلن داد (مثلاً پرداخت نقدی) | `plan`، `expires_at`، `quota_bytes` |
| `ticket.answered` | ادمین به تیکت جواب داد | `ticket_id`، `subject`، `body`، `has_image` |
| `quota.warning` | ۸۰٪ یا ۹۵٪ حجم مصرف شد | `percent`، `quota_bytes`، `used_bytes`، `remaining_bytes` |
| `quota.exhausted` | حجم تمام شد و سرویس قطع است | `quota_bytes`، `used_bytes` |
| `plan.expiring` | ۳ روز یا کمتر به پایان دوره مانده | `expires_at`، `days_left` |
| `plan.expired` | دوره تمام شد | `expires_at` |
| `password.reset_code` | مشتری در پنل وب «بازیابی رمز» زد | `code` (شش رقم)، `minutes` |
| `ping` | دکمهٔ «ارسال آزمایشی» | — (`telegram_id` خالی است) |

هر اتفاق **یک بار** خبر داده می‌شود و فقط برای مشتری‌ای که آیدی تلگرام دارد.

**تحویل:** هر جواب `2xx` یعنی رسید. غیر از آن (یا جواب ندادن در ۱۰ ثانیه) دوباره فرستاده می‌شود، با فاصله‌های ۳۰ ثانیه، ۱ دقیقه، ۲ دقیقه … تا حداکثر یک ساعت، روی هم حدود یک روز. پس ربات باید زود جواب بدهد و کار سنگین را بعداً بکند. ممکن است یک خبر دو بار برسد (مثلاً جواب ربات در راه گم شده)؛ `id` هر خبر ثابت است و ربات می‌تواند تکراری‌ها را کنار بگذارد.

**امضا:** سرآیند `X-DoctorDNS-Signature` این شکل است: `t=1758536043,v1=<hex>`، که `v1` برابر HMAC-SHA256 رشتهٔ `"<t>.<بدنهٔ خام>"` با رمز امضاست. ربات باید آن را چک کند و خبرهایی را که `t`شان خیلی قدیمی است (مثلاً بیش از ۵ دقیقه) نپذیرد. سرآیندهای `X-DoctorDNS-Event` و `X-DoctorDNS-Delivery` هم نوع خبر و `id` را دارند.

```python
import hashlib, hmac, time

def from_panel(secret, header, body):      # body: bytes، همان‌طور که رسیده
    parts = dict(p.split("=", 1) for p in header.split(","))
    want = hmac.new(secret.encode(), (parts["t"] + ".").encode() + body,
                    hashlib.sha256).hexdigest()
    return (hmac.compare_digest(want, parts.get("v1", ""))
            and abs(time.time() - int(parts["t"])) < 300)
```

## ربات ادمین — کارهای خود اپراتور

کلیدی که با تیک **«دسترسی ادمین»** ساخته شود (پنل ادمین → API → کلید تازه)، علاوه بر همهٔ دستورهای بالا به این‌ها هم دسترسی دارد. کلید معمولی برای این‌ها `403` با `admin_only` می‌گیرد. این کلید پول و حساب‌ها را جابه‌جا می‌کند: فقط برای ربات خودتان بسازید و حتماً به آی‌پی سرور ربات محدودش کنید.

اینجا کاربرها با **شمارهٔ حساب** (`id`) نام برده می‌شوند، نه آیدی تلگرام — بعضی مشتری‌ها تلگرام ندارند.

| | |
|---|---|
| `GET /admin/stats` | آمار: کاربرها به تفکیک وضعیت، ثبت‌نام امروز، رسید در انتظار، تأیید و درآمد امروز، درآمد ۷ روز، تیکت منتظر، دوره‌های نزدیک به پایان — و `text` آماده |
| `GET /admin/receipts?status=pending` | رسیدها؛ `status` یکی از `pending` (پیش‌فرض، قدیمی اول)، `approved`، `rejected`، `all` |
| `GET /admin/receipts/{id}/image` | عکس رسید: `{"content_type", "data"}` (بعد از تصمیم پاک می‌شود) |
| `POST /admin/receipts/{id}/approve` | تأیید — پلن رسید خودکار روی حساب می‌نشیند، دقیقاً مثل پنل |
| `POST /admin/receipts/{id}/reject` | رد |
| `GET /admin/users?q=...` | جستجو با نام، نام کاربری، شماره، آیدی تلگرام، شمارهٔ حساب یا آی‌پی (بیست تا)؛ بدون `q` تازه‌ترین‌ها |
| `GET /admin/users/{id}` | یک حساب |
| `POST /admin/users/{id}/status` | `{"status": "suspended"}` یا `{"status": "active"}` |
| `POST /admin/users/{id}/plan` | `{"plan_id": 1}` — دادن پلن بدون رسید (مثلاً پرداخت نقدی) |
| `GET /admin/tickets?status=open` | تیکت‌ها؛ `open` (پیش‌فرض)، `answered`، `closed`، `all` |
| `GET /admin/tickets/{id}` | یک تیکت با پیام‌ها و صاحبش |
| `POST /admin/tickets/{id}/reply` | جواب: `{"body": "...", "image_type"?, "image_data"?}` |
| `POST /admin/tickets/{id}/close` | بستن |

```sh
curl -s -X POST -H "Authorization: Bearer $ADMIN_KEY" $API/admin/receipts/31/approve
```
```json
{"ok": true, "message": "رسید تأیید شد؛ پلن «گیمینگ ماهانه» فعال شد تا 2026-10-22",
 "receipt": {"id": 31, "status": "approved", "amount": 200000, "plan": {...}, "user": {...}}}
```

رسیدی که قبلاً تصمیمش گرفته شده: `409` با `already_decided` — دو بار زدن دکمه چیزی را دو بار تمدید نمی‌کند. هر کار با اسم کلید در لاگ پنل ثبت می‌شود. تأیید رسید، جواب تیکت و دادن پلن، مثل پنل، به ربات مشتری هم خبر داده می‌شود.

**خبرهای ادمین** فقط به کلیدهای ادمینی که webhook دارند می‌رسد؛ `telegram_id` در آن‌ها خالی است و `"audience": "admin"` دارند:

| `event` | کی | در `data` |
|---|---|---|
| `receipt.submitted` | رسید تازه رسید | `receipt_id`، `user_id`، `amount`، `plan` |
| `ticket.opened` | تیکت تازه | `ticket_id`، `user_id`، `subject`، `body` |
| `ticket.message` | مشتری در تیکتی نوشت | `ticket_id`، `user_id`، `subject`، `body` |
| `user.created` | مشتری تازه ثبت‌نام کرد | `user_id`، `via` (`web` یا `bot`) |
| `trial.started` | مشتری تست رایگان گرفت | `user_id`، `plan` |
| `report.daily` | هر روز ساعت ۹ صبح به وقت ایران | همان عددهای `/admin/stats` |

هر کدام `text` فارسی آماده دارند، برای فرستادن به تلگرام خودتان.

## کدهای خطا

| HTTP | `error` | |
|---|---|---|
| 400 | `bad_json`، `bad_request` | بدنه JSON درستی نیست |
| 400 | `bad_telegram_id`، `bad_ip` | ورودی نادرست |
| 400 | `plan_required`، `bad_type`، `bad_file`، `empty_file` | رسید ناقص |
| 400 | `bad_code`، `code_expired` | کد اتصال تلگرام |
| 403 | `telegram_required` | ادمین خرید بدون تلگرام را بسته؛ مشتری پنل وب باید اول تلگرامش را وصل کند |
| 400 | `bad_idempotency_key` | بیش از ۱۰۰ نویسه |
| 400 | `subject_required`، `body_required` | تیکت بی‌موضوع یا بی‌متن |
| 401 | `unauthorized` | کلید نیست، اشتباه است یا باطل شده |
| 403 | `ip_not_allowed` | کلید از این آدرس پذیرفته نمی‌شود |
| 403 | `admin_only` | این کار کلید ادمین می‌خواهد |
| 404 | `not_found`، `user_not_found`، `ip_not_found`، `ticket_not_found`، `image_not_found` | |
| 405 | `method_not_allowed` | |
| 409 | `ip_taken`، `plan_not_on_sale`، `too_many_tickets`، `telegram_in_use`، `already_decided` | |
| 413 | `too_big` | بیش از حد بزرگ |
| 422 | `idempotency_key_reused` | |
| 429 | `slow_down` | کمی صبر؛ `Retry-After` می‌گوید چقدر |

خبر `password.reset_code` کدی دارد که با آن رمز حساب عوض می‌شود: فقط برای همان `telegram_id` بفرستید، جای دیگری نگهش ندارید و در لاگ ربات ننویسید. کد ۱۰ دقیقه و ۵ بار امتحان اعتبار دارد.
