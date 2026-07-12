"""Manager ("factory") bot.

Lets a user register their own Telegram bot token; the manager then launches a
dedicated anti-spam bot process for that token (see ``child_bot.py``) and keeps
it alive. All state lives in PostgreSQL + Redis via ``db``; child processes are
handled by ``process_manager``.

Run with the manager token in the environment:

    export MAIN_BOT_TOKEN=123456:ABC...
    export DATABASE_URL=postgresql://antispam:antispam@localhost:5432/antispam
    export REDIS_URL=redis://localhost:6379/0
    python3 antispambot.py
"""

from __future__ import annotations

import atexit
import signal
import time

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import db
import process_manager as pm

log = config.setup_logging("antispam.manager")

main_bot = telebot.TeleBot(config.get_main_bot_token())


def validate_bot_token(token: str) -> tuple[bool, str]:
    """Return (is_valid, username_or_error) for a candidate child-bot token."""
    try:
        info = telebot.TeleBot(token).get_me()
        return True, info.username
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@main_bot.message_handler(commands=["start"])
def handle_main_start(message) -> None:
    existing = db.get_user_bot(message.from_user.id)
    if existing:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🗑 حذف ربات", callback_data="delete_bot"))
        markup.add(InlineKeyboardButton("ℹ️ اطلاعات ربات", callback_data="bot_info"))
        main_bot.send_message(
            message.chat.id,
            "👋 خوش آمدید!\n\n✅ شما قبلاً یک ربات ساخته‌اید:\n"
            f"@{existing['bot_username']}\n\n"
            "برای ساخت ربات جدید، ابتدا ربات فعلی را حذف کنید.",
            reply_markup=markup,
        )
    else:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🤖 ساخت ربات انتی اسپم", callback_data="create_bot"))
        main_bot.send_message(
            message.chat.id,
            "👋 خوش آمدید به ربات ساز انتی اسپم!\n\n"
            "🔹 با این ربات می‌توانید یک ربات انتی اسپم اختصاصی برای گروه خود بسازید.\n\n"
            "🔸 ویژگی‌ها:\n"
            "• سیستم کپچا هوشمند\n• محدودیت تلاش\n• بن موقت و دائم\n• فقط یک گروه مجاز\n\n"
            "برای شروع روی دکمه زیر کلیک کنید:",
            reply_markup=markup,
        )


@main_bot.callback_query_handler(func=lambda call: True)
def handle_callback(call) -> None:
    user_id = call.from_user.id

    if call.data == "create_bot":
        main_bot.answer_callback_query(call.id)
        db.set_state(user_id, "waiting_token")
        main_bot.edit_message_text(
            "🔑 لطفاً توکن ربات تلگرام خود را ارسال کنید:\n\n"
            "⚠️ برای دریافت توکن:\n"
            "1. به @BotFather مراجعه کنید\n2. دستور /newbot را ارسال کنید\n"
            "3. نام و یوزرنیم ربات را تعیین کنید\n4. توکن دریافتی را اینجا ارسال کنید\n\n"
            "🔒 پیام حاوی توکن پس از دریافت حذف می‌شود.\n\n"
            "❌ برای لغو /cancel را ارسال کنید",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )

    elif call.data == "delete_bot":
        main_bot.answer_callback_query(call.id)
        bot_data = db.get_user_bot(user_id)
        if bot_data:
            pm.stop_bot(user_id)
            db.delete_user_bot(user_id)
            main_bot.edit_message_text(
                f"✅ ربات @{bot_data['bot_username']} با موفقیت حذف شد!\n\n"
                "برای ساخت ربات جدید /start را ارسال کنید.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
            )

    elif call.data == "bot_info":
        bot_data = db.get_user_bot(user_id)
        if bot_data:
            created = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(bot_data["created_at"])
            )
            main_bot.answer_callback_query(
                call.id,
                f"ربات: @{bot_data['bot_username']}\nتاریخ ساخت: {created}",
                show_alert=True,
            )
        else:
            main_bot.answer_callback_query(call.id)


@main_bot.message_handler(commands=["cancel"])
def handle_cancel(message) -> None:
    user_id = message.from_user.id
    if db.get_state(user_id):
        db.clear_state(user_id)
        main_bot.send_message(
            message.chat.id, "❌ عملیات لغو شد.\n\nبرای شروع مجدد /start را ارسال کنید."
        )
    else:
        main_bot.send_message(message.chat.id, "هیچ عملیاتی در انتظار نیست.")


@main_bot.message_handler(func=lambda m: db.get_state(m.from_user.id) == "waiting_token")
def handle_user_input(message) -> None:
    user_id = message.from_user.id
    bot_token = message.text.strip()

    # Remove the message carrying the secret token from the chat history.
    try:
        main_bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    if db.get_user_bot_by_token(bot_token):
        main_bot.send_message(message.chat.id, "❌ این توکن قبلاً استفاده شده است!")
        return

    msg = main_bot.send_message(message.chat.id, "⏳ در حال بررسی توکن...")
    is_valid, bot_username = validate_bot_token(bot_token)
    if not is_valid:
        main_bot.edit_message_text(
            f"❌ توکن نامعتبر است!\n\nخطا: {bot_username}\n\n"
            "لطفاً توکن صحیح را ارسال کنید یا /cancel برای لغو.",
            chat_id=message.chat.id,
            message_id=msg.message_id,
        )
        return

    main_bot.edit_message_text(
        "✅ توکن معتبر است!\n⏳ در حال ساخت ربات...",
        chat_id=message.chat.id,
        message_id=msg.message_id,
    )

    db.upsert_user_bot(user_id, bot_token, bot_username, time.time())
    pid = pm.start_bot(user_id, bot_token)
    db.clear_state(user_id)

    if pid and pm.is_running(user_id):
        main_bot.edit_message_text(
            "✅ ربات شما با موفقیت ساخته شد!\n\n"
            f"🤖 یوزرنیم ربات: @{bot_username}\n\n"
            "📋 مراحل راه‌اندازی:\n"
            "1️⃣ ربات را به گروه خود اضافه کنید\n"
            "2️⃣ ربات را ادمین کنید (با دسترسی حذف کاربران)\n"
            "3️⃣ گروه به صورت خودکار تنظیم می‌شود!\n\n"
            "✅ حالا ربات شما آماده است!\n\n"
            "⚠️ نکته: ربات فقط در یک گروه کار می‌کند. اگر به گروه دوم اضافه شود، خودکار خارج می‌شود.",
            chat_id=message.chat.id,
            message_id=msg.message_id,
        )
    else:
        main_bot.edit_message_text(
            "❌ خطا در راه‌اندازی ربات. لطفاً دوباره تلاش کنید.",
            chat_id=message.chat.id,
            message_id=msg.message_id,
        )


@main_bot.message_handler(commands=["mybots"])
def handle_mybots(message) -> None:
    bot_data = db.get_user_bot(message.from_user.id)
    if not bot_data:
        main_bot.send_message(
            message.chat.id,
            "❌ شما هیچ رباتی ندارید.\n\nبرای ساخت ربات /start را ارسال کنید.",
        )
        return

    created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(bot_data["created_at"]))
    group_status = (
        f"گروه تنظیم شده: {bot_data['group_id']}"
        if bot_data["group_id"]
        else "گروه تنظیم نشده"
    )
    running = "✅ در حال اجراست" if pm.is_running(message.from_user.id) else "⛔️ متوقف"

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🗑 حذف ربات", callback_data="delete_bot"))
    main_bot.send_message(
        message.chat.id,
        f"🤖 ربات شما:\n\n"
        f"👤 یوزرنیم: @{bot_data['bot_username']}\n"
        f"📅 تاریخ ساخت: {created}\n"
        f"📊 وضعیت: {group_status}\n"
        f"⚙️ پروسه: {running}",
        reply_markup=markup,
    )


@main_bot.message_handler(commands=["help"])
def handle_help(message) -> None:
    main_bot.send_message(
        message.chat.id,
        "📖 راهنمای استفاده:\n\n"
        "🔹 دستورات:\n/start - شروع و ساخت ربات\n/mybots - مشاهده ربات‌های من\n"
        "/help - نمایش این راهنما\n/cancel - لغو عملیات\n\n"
        "🔹 نحوه استفاده:\n"
        "1. دستور /start را ارسال کنید\n2. روی «ساخت ربات انتی اسپم» کلیک کنید\n"
        "3. توکن ربات را از @BotFather دریافت کنید\n4. توکن را ارسال کنید\n"
        "5. ربات را به گروه اضافه کنید و ادمین کنید\n6. گروه خودکار تنظیم می‌شود!\n\n"
        "🔹 ویژگی‌های ربات:\n"
        "✅ سیستم کپچا تصویری\n"
        f"✅ بن موقت بعد از {config.TEMP_BAN_AT_FAILS} تلاش اشتباه (1 ساعت)\n"
        f"✅ بن دائم بعد از {config.PERMANENT_BAN_AT_FAILS} تلاش اشتباه\n"
        "✅ حذف تایید هویت با خروج از گروه\n✅ محدودیت یک گروه (تنظیم خودکار)\n\n"
        "⚠️ نکات مهم:\n• ربات باید ادمین باشد\n• دسترسی حذف کاربران الزامی است\n"
        "• اولین گروهی که ربات به آن اضافه می‌شود، گروه مجاز است",
    )


def _install_shutdown_hooks() -> None:
    atexit.register(pm.shutdown_all)

    def _handle_signal(signum, _frame):
        log.info("Received signal %s, shutting down", signum)
        pm.shutdown_all()
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_signal)
        except ValueError:
            pass  # not on the main thread


def main() -> None:
    log.info("Main bot starting...")
    db.init_schema()
    _install_shutdown_hooks()

    log.info("Reconciling existing child bots...")
    pm.reconcile_on_startup()

    import threading

    threading.Thread(target=pm.monitor_loop, daemon=True).start()

    log.info("Bot manager is ready!")
    main_bot.infinity_polling(timeout=60, long_polling_timeout=60)


if __name__ == "__main__":
    main()
