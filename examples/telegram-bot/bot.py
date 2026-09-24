#!/usr/bin/env python3
"""doctor-dns sample Telegram bot.

A working bot on top of the panel's bot API - for customers, and for the
operator when their Telegram id is in ADMIN_IDS. Plain Python 3, nothing to
install. It talks to Telegram by long polling, so it needs no public address
of its own, and it listens on 127.0.0.1 for what the panel tells it.

Run it where Telegram is reachable - the exit server is the natural place.
Settings come from the environment (see bot.env.example):

    BOT_TOKEN        from @BotFather
    API_URL          https://<panel domain>:8445/api/v1
    API_KEY          a key from the admin panel's API page; with admin rights
                     if ADMIN_IDS is set
    WEBHOOK_SECRET   the key's webhook signing secret (whsec_...)
    LISTEN           where the panel's messages arrive, default 127.0.0.1:18990
    ADMIN_IDS        Telegram ids of the operator, comma separated (optional)
    PAY_TEXT         how to pay, only if the admin panel's Payment page is
                     empty - that one wins
    SUPPORT_TEXT     shown under "help" (optional)
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- settings
def settings(env=os.environ):
    cfg = {
        "token": env.get("BOT_TOKEN", "").strip(),
        "api": env.get("API_URL", "").strip().rstrip("/"),
        "key": env.get("API_KEY", "").strip(),
        "secret": env.get("WEBHOOK_SECRET", "").strip(),
        "listen": env.get("LISTEN", "127.0.0.1:18990").strip(),
        "admins": {int(x) for x in re.findall(r"\d+", env.get("ADMIN_IDS", ""))},
        "pay": env.get("PAY_TEXT", "").strip().replace("\\n", "\n"),
        "support": env.get("SUPPORT_TEXT", "").strip().replace("\\n", "\n"),
        "telegram": env.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/"),
    }
    missing = [k for k in ("token", "api", "key") if not cfg[k]]
    if missing:
        sys.exit("missing settings: %s (see bot.env.example)"
                 % ", ".join({"token": "BOT_TOKEN", "api": "API_URL", "key": "API_KEY"}[m]
                             for m in missing))
    return cfg


# The buttons of the customer's keyboard.
B_ACCOUNT = "📊 حساب من"
B_BUY = "🛒 خرید / تمدید"
B_IP = "🌐 ثبت آی‌پی"
B_SUPPORT = "🎫 پشتیبانی"
B_HELP = "❓ راهنما"
B_WEB = "🔑 پنل وب"
B_DOH = "🔒 DNS امن"
B_CANCEL = "انصراف"
MENU = {"keyboard": [[B_ACCOUNT, B_BUY], [B_IP, B_DOH], [B_SUPPORT, B_WEB], [B_HELP]],
        "resize_keyboard": True}
CANCEL = {"keyboard": [[B_CANCEL]], "resize_keyboard": True}
STATUS = {"pending": "در انتظار خرید پلن", "active": "فعال ✅",
          "over_quota": "حجم تمام شده ⛔", "expired": "دوره تمام شده ⛔",
          "suspended": "مسدود ⛔"}
MAX_FILE = 4 * 1024 * 1024


def size_fa(n):
    n = float(n or 0)
    for unit in ("بایت", "کیلوبایت", "مگابایت", "گیگابایت", "ترابایت"):
        if n < 1024 or unit == "ترابایت":
            return ("%d %s" if unit == "بایت" else "%.1f %s") % (n, unit)
        n /= 1024


def image_type(blob):
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[:5] == b"%PDF-":
        return "application/pdf"
    return None


class ApiError(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(body.get("message") or body.get("error") or "HTTP %d" % status)


# ------------------------------------------------------------ the two APIs
class Panel:
    """The panel's bot API."""

    def __init__(self, cfg):
        self.base, self.key = cfg["api"], cfg["key"]

    def call(self, method, path, body=None, idem=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None
                                     else None)
        req.add_header("Authorization", "Bearer " + self.key)
        req.add_header("Content-Type", "application/json")
        if idem:
            req.add_header("Idempotency-Key", idem)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except ValueError:
                body = {"message": "پنل جواب درستی نداد (HTTP %d)" % e.code}
            raise ApiError(e.code, body)


class Telegram:
    def __init__(self, cfg):
        self.base = "%s/bot%s/" % (cfg["telegram"], cfg["token"])
        self.files = "%s/file/bot%s/" % (cfg["telegram"], cfg["token"])

    def call(self, method, http_timeout=30, **params):
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(self.base + method, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            res = json.loads(e.read() or b"{}")
        if not res.get("ok"):
            raise RuntimeError("telegram %s: %s" % (method, res.get("description")))
        return res.get("result")

    def send(self, chat, text, markup=None):
        return self.call("sendMessage", chat_id=chat, text=text[:4000], reply_markup=markup,
                         link_preview_options={"is_disabled": True})

    def photo(self, chat, blob, caption, markup=None):
        """sendPhoto needs a real upload, so this one is multipart."""
        boundary = secrets.token_hex(16)
        parts = []
        for name, value in (("chat_id", str(chat)), ("caption", caption[:1000]),
                            ("reply_markup", json.dumps(markup) if markup else None)):
            if value is not None:
                parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                              % (boundary, name, value)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="photo"; '
                      'filename="receipt"\r\nContent-Type: application/octet-stream\r\n\r\n'
                      % boundary).encode() + blob + b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode())
        req = urllib.request.Request(self.base + "sendPhoto", data=b"".join(parts),
                                     headers={"Content-Type": "multipart/form-data; boundary="
                                              + boundary})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read()).get("result")
        except urllib.error.HTTPError:
            # A PDF, or something Telegram will not show as a photo: say it in words.
            return self.send(chat, caption + "\n\n(فایل رسید عکس نبود؛ در پنل ببینید)", markup)

    def download(self, file_id):
        info = self.call("getFile", file_id=file_id)
        if (info.get("file_size") or 0) > MAX_FILE:
            raise ValueError("فایل بزرگ‌تر از ۴ مگابایت است")
        with urllib.request.urlopen(self.files + info["file_path"], timeout=60) as r:
            return r.read(MAX_FILE + 1)


# ------------------------------------------------------------------ the bot
class Bot:
    def __init__(self, cfg, panel=None, telegram=None):
        self.cfg = cfg
        self.panel = panel or Panel(cfg)
        self.tg = telegram or Telegram(cfg)
        self.state = {}          # chat id -> (what we wait for, details)
        self.known = set()       # telegram ids the panel already has an account for
        self.seen = []           # recent webhook ids, so a repeat is dropped
        self.lock = threading.Lock()

    # -- helpers ------------------------------------------------------------
    def is_admin(self, uid):
        return uid in self.cfg["admins"]

    def say(self, chat, text, markup=None):
        try:
            self.tg.send(chat, text, markup)
        except Exception as e:
            log("could not send to %s: %s" % (chat, e))

    def account(self, sender):
        """The customer's account, opened on first use. Not on /start: a
        customer arriving to link a web account must not get a second one."""
        uid = sender["id"]
        if uid not in self.known:
            name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
            self.panel.call("POST", "/users", {"telegram_id": uid, "name": name[:60]})
            self.known.add(uid)
        return self.panel.call("GET", "/users/%d" % uid)["user"]

    # -- updates from Telegram ------------------------------------------------
    def handle(self, update):
        try:
            if "callback_query" in update:
                return self.on_button(update["callback_query"])
            msg = update.get("message")
            if msg and msg.get("chat", {}).get("type") == "private":
                return self.on_message(msg)
        except ApiError as e:
            chat = (update.get("message") or update.get("callback_query", {}).get("message")
                    or {}).get("chat", {}).get("id")
            if chat:
                self.say(chat, "⚠️ " + str(e))
        except Exception:
            log("update failed:\n" + traceback.format_exc())

    def on_message(self, msg):
        chat, sender = msg["chat"]["id"], msg["from"]
        text = (msg.get("text") or "").strip()
        if text == B_CANCEL or text == "/cancel":
            self.state.pop(chat, None)
            return self.say(chat, "لغو شد.", MENU)
        if text.startswith("/start"):
            return self.on_start(chat, sender, text)
        if self.is_admin(sender["id"]) and text in ("/stats", "/receipts"):
            return self.admin_command(chat, text)

        waiting, extra = self.state.get(chat, (None, None))
        if waiting == "onb_name":
            return self.got_name(chat, sender, text)
        if waiting == "onb_user":
            return self.got_username(chat, sender, text, extra)
        if text == "/doh":
            text = B_DOH
        if text in (B_ACCOUNT, B_BUY, B_IP, B_DOH, B_SUPPORT, B_WEB) and not self.ready(chat, sender):
            return
        if waiting == "ip":
            return self.got_ip(chat, sender, text)
        if waiting == "receipt":
            return self.got_receipt(chat, sender, msg, extra)
        if waiting == "ticket_subject" and text:
            self.state[chat] = ("ticket_body", text[:80])
            return self.say(chat, "متن پیامتان را بنویسید (می‌توانید عکس هم با توضیح بفرستید):",
                            CANCEL)
        if waiting == "ticket_body":
            return self.got_ticket(chat, sender, msg, subject=extra)
        if waiting == "ticket_reply":
            return self.got_ticket(chat, sender, msg, ticket=extra)
        if waiting == "admin_reply" and self.is_admin(sender["id"]):
            return self.admin_reply(chat, msg, extra)

        if text == B_ACCOUNT:
            return self.show_account(chat, sender)
        if text == B_BUY:
            return self.show_plans(chat, sender)
        if text == B_IP:
            return self.ip_help(chat, sender)
        if text == B_WEB:
            return self.show_web(chat, sender)
        if text == B_DOH:
            return self.show_doh(chat, sender)
        if text == B_SUPPORT:
            return self.show_tickets(chat, sender)
        if text == B_HELP:
            return self.say(chat, self.help_text(), MENU)
        return self.say(chat, "از دکمه‌های پایین استفاده کنید.", MENU)

    def on_start(self, chat, sender, text):
        self.state.pop(chat, None)
        arg = text.split(" ", 1)[1].strip() if " " in text else ""
        if arg.startswith("link_"):
            try:
                res = self.panel.call("POST", "/link", {"telegram_id": sender["id"],
                                                        "code": arg[5:]})
                self.known.add(sender["id"])
                return self.say(chat, "✅ " + res["message"] + "\nحالا اگر رمز پنل را فراموش "
                                "کنید، کد بازیابی همین‌جا می‌آید.", MENU)
            except ApiError as e:
                return self.say(chat, "⚠️ " + str(e), MENU)
        return self.say(chat, "سلام! 👋 به ربات خوش آمدید.\n\n" + self.help_text(), MENU)

    # -- a web sign-in for everybody who comes through the bot ----------------
    def ready(self, chat, sender):
        """True when the account has its web sign-in. Otherwise the two
        questions start, and whatever was pressed waits until they are done."""
        u = self.account(sender)
        if u.get("username"):
            return True
        tg_name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
        self.state[chat] = ("onb_name", None)
        self.say(chat, "اول حسابتان را کامل کنیم 🙂\n\nاسمتان چیست؟",
                 {"keyboard": [[tg_name[:60]]] if tg_name else [[B_CANCEL]],
                  "resize_keyboard": True, "one_time_keyboard": True})
        return False

    def got_name(self, chat, sender, text):
        if not text or text == B_CANCEL:
            self.state.pop(chat, None)
            return self.say(chat, "هر وقت خواستید دوباره یکی از دکمه‌ها را بزنید.", MENU)
        self.state[chat] = ("onb_user", text[:60])
        self.say(chat, "یک نام کاربری انگلیسی برای ورود به پنل وب انتخاب کنید "
                 "(حروف انگلیسی و عدد، مثلاً ali_gamer):", CANCEL)

    def got_username(self, chat, sender, text, name):
        try:
            res = self.panel.call("POST", "/users/%d/credentials" % sender["id"],
                                  {"username": text, "name": name})
        except ApiError as e:
            if e.body.get("error") == "has_username":
                self.state.pop(chat, None)
                return self.show_web(chat, sender)
            return self.say(chat, "⚠️ %s" % e, CANCEL)
        self.state.pop(chat, None)
        self.say(chat, "✅ حساب پنل شما ساخته شد\n\n%sنام کاربری: %s\nرمز: %s\n\nاین را "
                 "نگه دارید؛ رمز را در پنل هر وقت خواستید عوض کنید."
                 % ("آدرس: %s\n" % res["panel_url"] if res.get("panel_url") else "",
                    res["username"], res["password"]), MENU)
        self.ip_help(chat, sender)

    def login_button(self, sender):
        try:
            res = self.panel.call("POST", "/users/%d/login-link" % sender["id"])
        except ApiError:
            return None
        return res

    def ip_help(self, chat, sender):
        """Registering the address is easiest from the web page, which sees it
        by itself: a one-time link signs the customer straight in."""
        link = self.login_button(sender)
        self.state[chat] = ("ip", None)
        if link:
            return self.say(chat, "🌐 ثبت آی‌پی\n\nاین لینک را با همان اینترنتی باز کنید که "
                            "می‌خواهید سرویس رویش کار کند (مثلاً وای‌فای خانه، نه اینترنت "
                            "گوشی)؛ خودکار وارد حسابتان می‌شوید و فقط «ثبت آی‌پی» را بزنید:\n\n"
                            "%s\n\n(%d دقیقه اعتبار دارد و یک بار کار می‌کند)\n\n"
                            "یا آی‌پی را همین‌جا بفرستید." % (link["url"], link["minutes"]),
                            CANCEL)
        return self.say(chat, "آی‌پی اینترنتی را که می‌خواهید سرویس رویش کار کند بفرستید.\n\n"
                        "روی همان اینترنت (مودم یا سیم‌کارت) یک سایت «آی‌پی من چیست» را "
                        "باز کنید تا پیدایش کنید. مثال: 5.123.45.67", CANCEL)

    def show_web(self, chat, sender):
        u = self.account(sender)
        self.say(chat, "🔑 پنل وب\n\n%sنام کاربری: %s\n\nرمز را فراموش کرده‌اید؟ «رمز تازه» "
                 "را بزنید." % ("آدرس: %s\n" % u["panel_url"] if u.get("panel_url") else "",
                              u["username"]),
                 {"inline_keyboard": [[{"text": "🔗 ورود با یک کلیک", "callback_data": "login"},
                                       {"text": "🔄 رمز تازه", "callback_data": "newpw"}]]})

    def show_doh(self, chat, sender):
        """The customer's personal encrypted-DNS addresses. The panel sends
        them only once a relay has DoH on; the iPhone profile is on the web
        page, which is what the login button is for."""
        u = self.account(sender)
        doh = u.get("doh")
        if not doh:
            return self.say(chat, "🔒 DNS امن هنوز روی این سرویس فعال نیست. از DNS معمولی "
                            "(«حساب من») استفاده کنید.", MENU)
        lines = ["🔒 DNS امن (رمزگذاری‌شده)",
                 "برای وقتی که اپراتور DNS را می‌رباید یا دست‌کاری می‌کند. مثل DNS معمولی "
                 "فقط روی اینترنتی کار می‌کند که آی‌پی‌اش را ثبت کرده‌اید.",
                 "",
                 "📱 اندروید — تنظیمات ← شبکه ← DNS خصوصی ← نام میزبان:",
                 doh["dot_host"],
                 "",
                 "💻 آیفون، ویندوز، کروم و فایرفاکس — آدرس شخصی شما:",
                 doh["url"],
                 "",
                 "پروفایل آماده‌ی آیفون در پنل وب، بخش «DNS رمزگذاری‌شده» است.",
                 "این آدرس مخصوص حساب شماست؛ آن را به کسی ندهید."]
        if not u["ips"]:
            lines.append("\n⚠️ هنوز آی‌پی ثبت نکرده‌اید؛ بدون آن کار نمی‌کند.")
        self.say(chat, "\n".join(lines),
                 {"inline_keyboard": [[{"text": "🔗 ورود به پنل وب", "callback_data": "login"},
                                       {"text": "🔄 آدرس تازه", "callback_data": "dohnew"}]]})

    def help_text(self):
        return ("📊 حساب من: وضعیت، حجم مانده و آدرس DNS\n"
                "🛒 خرید / تمدید: انتخاب پلن و فرستادن رسید\n"
                "🌐 ثبت آی‌پی: سرویس فقط روی آی‌پی ثبت‌شده کار می‌کند\n"
                "🎫 پشتیبانی: تیکت و گفتگو با پشتیبانی\n"
                "🔒 DNS امن: آدرس DoH و DoT، برای وقتی اپراتور DNS را دست‌کاری می‌کند\n"
                "🔑 پنل وب: نام کاربری، ورود با یک کلیک و رمز تازه\n\n"
                "حساب پنل وب دارید؟ در پنل «اتصال حساب به تلگرام» را بزنید و کد را "
                "همین‌جا بفرستید." + ("\n\n" + self.cfg["support"] if self.cfg["support"] else ""))

    # -- the customer's screens ---------------------------------------------
    def show_account(self, chat, sender):
        u = self.account(sender)
        lines = ["👤 %s" % (u["name"] or "حساب شما"),
                 "وضعیت: %s" % STATUS.get(u["status"], u["status"])]
        if u["plan"]:
            lines.append("پلن: %s" % u["plan"]["name"])
        if u["status"] != "pending":
            lines.append("حجم مانده: %s" % ("نامحدود" if u["unlimited"]
                                              else size_fa(u["remaining_bytes"])))
            lines.append("مصرف: %s" % size_fa(u["used_bytes"]))
        if u["expires_at"]:
            lines.append("پایان دوره: %s" % u["expires_at"][:10])
        lines.append("آی‌پی: %s" % (", ".join(u["ips"]) if u["ips"] else "ثبت نشده ⚠️"))
        if u["dns"]:
            lines.append("\nDNS: %s\nاین آدرس را در کنسول یا مودم، هم برای DNS اول و هم دوم، "
                         "بگذارید." % u["dns"][0])
        if u["receipt_waiting"]:
            lines.append("\n⏳ یک رسید در انتظار بررسی دارید.")
        self.say(chat, "\n".join(lines), MENU)

    def show_plans(self, chat, sender):
        u = self.account(sender)
        plans = self.panel.call("GET", "/plans")["plans"]
        trial = u.get("trial")
        rows = []
        if trial and trial.get("available"):
            p = trial["plan"]
            rows.append([{"text": "🎁 تست رایگان: %s، %d روز" % (
                size_fa(p["quota_bytes"]) if p["quota_bytes"] else "نامحدود", p["days"]),
                "callback_data": "trial"}])
        if not plans and not rows:
            return self.say(chat, "فعلاً پلنی برای فروش نیست. با پشتیبانی در تماس باشید.", MENU)
        for p in plans:
            size = size_fa(p["quota_bytes"]) if p["quota_bytes"] else "نامحدود"
            rows.append([{"text": "%s · %s · %d روز · %s تومان"
                          % (p["name"], size, p["days"], format(p["price"], ",")),
                          "callback_data": "buy:%d" % p["id"]}])
        notes = "\n".join("• %s (%s): %s" % (p["name"], p["template"], p["note"])
                          for p in plans if p.get("note"))
        self.say(chat, "پلن را انتخاب کنید:" + ("\n\n" + notes if notes else ""),
                 {"inline_keyboard": rows})

    def plan_games(self, plan, most=12):
        """The games a plan covers, as the customer reads them.

        The panel sends the names; a long list is cut off here, because a
        message that scrolls past the price is not an answer to "what is in
        it". The count that follows says the rest is still there.
        """
        games = plan.get("games") or []
        if not games:
            return ""
        # One more name is shorter than saying there is one more.
        shown = games if len(games) <= most + 1 else games[:most]
        rest = len(games) - len(shown)
        line = "، ".join(shown)
        if rest > 0:
            line += " و %d بازی دیگر" % rest
        return "\n\n🎮 شامل: " + line

    def chose_plan(self, chat, sender, plan_id):
        sale = self.panel.call("GET", "/plans")
        plans = {p["id"]: p for p in sale["plans"]}
        # What the admin panel says, first: changing the card there changes it
        # here at once. PAY_TEXT is only for a panel that has none set.
        pay = (sale.get("pay_text") or "").strip() or self.cfg["pay"]
        plan = plans.get(plan_id)
        if not plan:
            return self.say(chat, "این پلن دیگر فروخته نمی‌شود.", MENU)
        u = self.account(sender)
        warn = ""
        if u["plan"] and u["plan"]["id"] != plan_id and u["status"] in ("active", "over_quota"):
            warn = ("\n\n⚠️ پلن فعلی شما «%s» است. پلن تازه از لحظهٔ تأیید از نو شروع می‌شود "
                    "و باقی‌ماندهٔ پلن فعلی از بین می‌رود." % u["plan"]["name"])
        elif u["plan"] and u["plan"]["id"] == plan_id and u["status"] in ("active", "over_quota"):
            warn = "\n\n✅ تمدید همان پلن: روزها و حجم روی باقی‌مانده‌تان اضافه می‌شود."
        self.state[chat] = ("receipt", plan_id)
        self.say(chat, "پلن «%s» — %s تومان%s%s\n\n%s\n\nبعد از واریز، عکس رسید را همین‌جا "
                 "بفرستید." % (plan["name"], format(plan["price"], ","),
                               self.plan_games(plan), warn,
                               pay or "برای روش پرداخت با پشتیبانی تماس بگیرید."),
                 CANCEL)

    def got_receipt(self, chat, sender, msg, plan_id):
        file_id = None
        if msg.get("photo"):
            file_id = msg["photo"][-1]["file_id"]
        elif msg.get("document"):
            file_id = msg["document"]["file_id"]
        if not file_id:
            return self.say(chat, "عکس رسید را بفرستید (یا «انصراف»).", CANCEL)
        try:
            blob = self.tg.download(file_id)
        except ValueError as e:
            return self.say(chat, "⚠️ %s" % e, CANCEL)
        kind = image_type(blob)
        if not kind or len(blob) > MAX_FILE:
            return self.say(chat, "⚠️ فقط عکس (JPG، PNG، WEBP) یا PDF تا ۴ مگابایت.", CANCEL)
        # One key per Telegram message: a retry after a timeout is the same receipt.
        res = self.panel.call("POST", "/users/%d/receipts" % sender["id"],
                              {"plan_id": plan_id, "content_type": kind,
                               "data": base64.b64encode(blob).decode()},
                              idem="receipt-%d-%d" % (sender["id"], msg["message_id"]))
        self.state.pop(chat, None)
        self.say(chat, "✅ " + res["message"], MENU)

    def got_ip(self, chat, sender, text):
        self.account(sender)
        ip = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789.")).strip()
        try:
            res = self.panel.call("POST", "/users/%d/ips" % sender["id"], {"ip": ip})
        except ApiError as e:
            return self.say(chat, "⚠️ %s\nدوباره بفرستید یا «انصراف»." % e, CANCEL)
        self.state.pop(chat, None)
        self.say(chat, "✅ %s\n\nDNS را روی %s بگذارید." % (
            res["message"], res["user"]["dns"][0] if res["user"]["dns"] else "آدرس سرویس"), MENU)

    def show_tickets(self, chat, sender):
        self.account(sender)
        tickets = self.panel.call("GET", "/users/%d/tickets" % sender["id"])["tickets"]
        state = {"open": "⏳", "answered": "💬", "closed": "✔️"}
        rows = [[{"text": "%s %s" % (state.get(t["status"], ""), t["subject"]),
                  "callback_data": "tk:%d" % t["id"]}] for t in tickets[:8]]
        rows.append([{"text": "➕ تیکت تازه", "callback_data": "tknew"}])
        self.say(chat, "تیکت‌های شما:" if tickets else "هنوز تیکتی ندارید.",
                 {"inline_keyboard": rows})

    def show_ticket(self, chat, sender, tid):
        t = self.panel.call("GET", "/users/%d/tickets/%d" % (sender["id"], tid))["ticket"]
        lines = ["🎫 %s" % t["subject"]]
        for m in t["messages"][-10:]:
            lines.append("\n%s %s:\n%s%s" % ("👨‍💻" if m["from"] == "admin" else "👤",
                                              "پشتیبانی" if m["from"] == "admin" else "شما",
                                              m["body"], " 🖼" if m["has_image"] else ""))
        buttons = [{"text": "✍️ پیام تازه", "callback_data": "tkr:%d" % tid}]
        if t["status"] != "closed":
            buttons.append({"text": "✔️ بستن", "callback_data": "tkc:%d" % tid})
        self.say(chat, "\n".join(lines), {"inline_keyboard": [buttons]})

    def got_ticket(self, chat, sender, msg, subject=None, ticket=None):
        body = (msg.get("text") or msg.get("caption") or "").strip()
        if not body:
            return self.say(chat, "متن پیام را بنویسید (یا «انصراف»).", CANCEL)
        payload = {"body": body}
        if msg.get("photo"):
            blob = self.tg.download(msg["photo"][-1]["file_id"])
            if image_type(blob) in ("image/jpeg", "image/png", "image/webp"):
                payload.update(image_type=image_type(blob),
                               image_data=base64.b64encode(blob).decode())
        idem = "ticket-%d-%d" % (sender["id"], msg["message_id"])
        if subject is not None:
            payload["subject"] = subject
            res = self.panel.call("POST", "/users/%d/tickets" % sender["id"], payload, idem)
        else:
            res = self.panel.call("POST", "/users/%d/tickets/%d/messages"
                                  % (sender["id"], ticket), payload, idem)
        self.state.pop(chat, None)
        self.say(chat, "✅ " + res["message"], MENU)

    # -- buttons ------------------------------------------------------------
    def on_button(self, q):
        chat, sender, data = q["message"]["chat"]["id"], q["from"], q.get("data") or ""
        try:
            self.tg.call("answerCallbackQuery", callback_query_id=q["id"])
        except Exception:
            pass
        kind, _, arg = data.partition(":")
        if kind in ("ok", "no", "areply", "aclose"):
            if not self.is_admin(sender["id"]):
                return
            return self.admin_button(chat, q, kind, int(arg))
        if kind == "buy":
            return self.chose_plan(chat, sender, int(arg))
        if kind == "plans":
            return self.show_plans(chat, sender)
        if kind == "login":
            link = self.login_button(sender)
            return self.say(chat, ("🔗 %s\n\n(%d دقیقه اعتبار دارد و یک بار کار می‌کند)"
                                   % (link["url"], link["minutes"])) if link
                            else "⚠️ آدرس پنل هنوز معلوم نیست؛ چند دقیقه دیگر امتحان کنید.")
        if kind == "dohnew":
            self.panel.call("POST", "/users/%d/doh-reset" % sender["id"])
            self.say(chat, "🔄 آدرس تازه ساخته شد. آدرس قبلی تا یک دقیقه دیگر کار نمی‌کند؛ "
                     "این را روی دستگاه‌هایتان بگذارید:")
            return self.show_doh(chat, sender)
        if kind == "newpw":
            res = self.panel.call("POST", "/users/%d/password" % sender["id"])
            return self.say(chat, "🔄 رمز تازهٔ پنل: %s\nنام کاربری: %s\n\nهر جا با رمز قبلی "
                            "وارد بودید، خارج شدید." % (res["password"], res["username"]))
        if kind == "trial":
            self.account(sender)
            res = self.panel.call("POST", "/users/%d/trial" % sender["id"])
            self.say(chat, res["message"], MENU)
            if not res["user"]["ips"]:
                return self.ip_help(chat, sender)
            return None
        if kind == "tk":
            return self.show_ticket(chat, sender, int(arg))
        if kind == "tknew":
            self.state[chat] = ("ticket_subject", None)
            return self.say(chat, "موضوع تیکت را در یک خط بنویسید:", CANCEL)
        if kind == "tkr":
            self.state[chat] = ("ticket_reply", int(arg))
            return self.say(chat, "پیامتان را بنویسید:", CANCEL)
        if kind == "tkc":
            res = self.panel.call("POST", "/users/%d/tickets/%d/close" % (sender["id"], int(arg)))
            return self.say(chat, "✔️ " + res["message"], MENU)

    # -- the operator -------------------------------------------------------
    def admin_command(self, chat, text):
        if text == "/stats":
            return self.say(chat, self.panel.call("GET", "/admin/stats")["text"])
        receipts = self.panel.call("GET", "/admin/receipts")["receipts"]
        if not receipts:
            return self.say(chat, "رسیدی در انتظار نیست.")
        for r in receipts[:10]:
            self.post_receipt(chat, r["id"], "رسید از %s%s — %s تومان" % (
                r["user"]["label"], " برای «%s»" % r["plan"]["name"] if r["plan"] else "",
                format(r["amount"], ",")))

    def post_receipt(self, chat, rid, caption):
        buttons = {"inline_keyboard": [[{"text": "✅ تأیید", "callback_data": "ok:%d" % rid},
                                        {"text": "❌ رد", "callback_data": "no:%d" % rid}]]}
        try:
            pic = self.panel.call("GET", "/admin/receipts/%d/image" % rid)
            self.tg.photo(chat, base64.b64decode(pic["data"]), caption, buttons)
        except ApiError:
            self.say(chat, caption, buttons)

    def admin_button(self, chat, q, kind, num):
        if kind in ("ok", "no"):
            try:
                res = self.panel.call("POST", "/admin/receipts/%d/%s"
                                      % (num, "approve" if kind == "ok" else "reject"))
                done = ("✅ " if kind == "ok" else "❌ ") + res["message"]
            except ApiError as e:
                done = "⚠️ " + str(e)
            # Take the buttons off, so nobody presses them again.
            try:
                self.tg.call("editMessageReplyMarkup", chat_id=chat,
                             message_id=q["message"]["message_id"],
                             reply_markup={"inline_keyboard": []})
            except Exception:
                pass
            return self.say(chat, "رسید #%d: %s" % (num, done))
        if kind == "areply":
            self.state[chat] = ("admin_reply", num)
            return self.say(chat, "جواب تیکت #%d را بنویسید:" % num, CANCEL)
        if kind == "aclose":
            res = self.panel.call("POST", "/admin/tickets/%d/close" % num)
            return self.say(chat, "تیکت #%d: %s" % (num, res["message"]))

    def admin_reply(self, chat, msg, tid):
        body = (msg.get("text") or msg.get("caption") or "").strip()
        if not body:
            return self.say(chat, "متن جواب را بنویسید (یا «انصراف»).", CANCEL)
        payload = {"body": body}
        if msg.get("photo"):
            blob = self.tg.download(msg["photo"][-1]["file_id"])
            if image_type(blob) in ("image/jpeg", "image/png", "image/webp"):
                payload.update(image_type=image_type(blob),
                               image_data=base64.b64encode(blob).decode())
        self.panel.call("POST", "/admin/tickets/%d/reply" % tid, payload,
                        idem="areply-%d-%d" % (chat, msg["message_id"]))
        self.state.pop(chat, None)
        self.say(chat, "✅ جواب تیکت #%d فرستاده شد." % tid, MENU)

    # -- what the panel tells us -------------------------------------------
    def on_event(self, ev):
        with self.lock:
            if ev.get("id") in self.seen:
                return
            self.seen = (self.seen + [ev.get("id")])[-1000:]
        data, kind = ev.get("data") or {}, ev.get("event")
        text = data.get("text") or ""
        if ev.get("audience") == "admin" or kind == "ping":
            for admin in self.cfg["admins"]:
                if kind == "receipt.submitted":
                    self.post_receipt(admin, data["receipt_id"], "🧾 " + text)
                elif kind in ("ticket.opened", "ticket.message"):
                    tid = data["ticket_id"]
                    self.say(admin, "🎫 #%d %s" % (tid, text), {"inline_keyboard": [[
                        {"text": "✍️ جواب", "callback_data": "areply:%d" % tid},
                        {"text": "✔️ بستن", "callback_data": "aclose:%d" % tid}]]})
                elif text:
                    self.say(admin, text)
            return
        chat = ev.get("telegram_id")
        if not chat or not text:
            return
        markup = None
        if kind == "ticket.answered":
            markup = {"inline_keyboard": [[{"text": "✍️ جواب",
                                            "callback_data": "tkr:%d" % data["ticket_id"]}]]}
        elif kind in ("quota.warning", "quota.exhausted", "plan.expiring", "plan.expired"):
            markup = {"inline_keyboard": [[{"text": "🛒 تمدید", "callback_data": "plans"}]]}
        self.say(chat, text, markup)


# ---------------------------------------------------------- panel webhooks
def signed(secret, header, body, now=None):
    """The panel's signature: t=<time>,v1=HMAC-SHA256("<t>.<body>")."""
    try:
        parts = dict(p.split("=", 1) for p in (header or "").split(","))
        stamp = int(parts["t"])
    except (ValueError, KeyError):
        return False
    want = hmac.new(secret.encode(), ("%d." % stamp).encode() + body,
                    hashlib.sha256).hexdigest()
    return (hmac.compare_digest(want, parts.get("v1", ""))
            and abs((now or time.time()) - stamp) < 300)


def webhook_server(bot, work):
    host, _, port = bot.cfg["listen"].rpartition(":")

    class Hook(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, 1024 * 1024))
            if not signed(bot.cfg["secret"], self.headers.get("X-DoctorDNS-Signature"), body):
                self.send_response(401)
                self.end_headers()
                return
            # Answer at once; Telegram calls happen after, on the worker.
            self.send_response(200)
            self.end_headers()
            try:
                work.put(json.loads(body))
            except ValueError:
                pass

        def log_message(self, *a):
            pass

    return http.server.ThreadingHTTPServer((host or "127.0.0.1", int(port)), Hook)


def main():
    cfg = settings()
    if not cfg["secret"]:
        log("WEBHOOK_SECRET is empty: the panel's messages will be refused")
    bot = Bot(cfg)
    me = bot.tg.call("getMe")
    log("bot @%s up; panel messages on %s; %d admin(s)"
        % (me.get("username"), cfg["listen"], len(cfg["admins"])))
    work = queue.Queue()

    def events():
        while True:
            ev = work.get()
            try:
                bot.on_event(ev)
            except Exception:
                log("event failed:\n" + traceback.format_exc())

    threading.Thread(target=events, daemon=True).start()
    server = webhook_server(bot, work)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    offset = None
    while True:
        try:
            # Long polling: Telegram holds the request up to 50 seconds.
            updates = bot.tg.call("getUpdates", http_timeout=70, timeout=50, offset=offset,
                                  allowed_updates=["message", "callback_query"])
        except Exception as e:
            log("getUpdates: %s" % e)
            time.sleep(5)
            continue
        # In order: a customer's photo and the text after it must not race.
        for u in updates or []:
            offset = u["update_id"] + 1
            bot.handle(u)


if __name__ == "__main__":
    main()
