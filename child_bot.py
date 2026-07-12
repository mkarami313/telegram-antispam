"""Standalone anti-spam bot.

Unlike the original design, this file is **static**: the manager runs the exact
same module for every user and injects per-bot configuration through environment
variables (``BOT_TOKEN``, ``OWNER_ID``). Nothing secret is ever written to disk.

Behaviour: new members are muted on join and must solve an image captcha in the
bot's PM to regain the ability to post. Wrong answers escalate to a temporary
and then a permanent ban.
"""

from __future__ import annotations

import os
import time

import telebot
from telebot.types import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)

import captcha
import config
import db

log = config.setup_logging("antispam.child")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
if not BOT_TOKEN or not OWNER_ID:
    raise RuntimeError("child_bot requires BOT_TOKEN and OWNER_ID environment variables")

bot = telebot.TeleBot(BOT_TOKEN)

# Permissions granted to a member once verified: full messaging, but no
# pinning or inviting by default (those stay with admins).
MEMBER_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=False,
    can_pin_messages=False,
)
MUTED_PERMISSIONS = ChatPermissions(can_send_messages=False)

# Cached bot identity, populated once at startup.
BOT_ID: int = 0
BOT_USERNAME: str = ""


def init_identity() -> None:
    """Populate BOT_ID / BOT_USERNAME, retrying in a loop (never recursively)."""
    global BOT_ID, BOT_USERNAME
    delay = 5
    while True:
        try:
            me = bot.get_me()
            BOT_ID = me.id
            BOT_USERNAME = me.username
            log.info("Bot identity: @%s (%s)", BOT_USERNAME, BOT_ID)
            return
        except Exception as exc:
            log.error("Error getting bot identity: %s (retrying in %ss)", exc, delay)
            time.sleep(delay)
            delay = min(delay * 2, 60)


def _unmute(group_id: int, user_id: int) -> None:
    bot.restrict_chat_member(
        group_id, user_id, permissions=MEMBER_PERMISSIONS,
        use_independent_chat_permissions=True,
    )


# --------------------------------------------------------------------------- #
# Group events
# --------------------------------------------------------------------------- #
@bot.message_handler(content_types=["new_chat_members"])
def handle_new_member(message) -> None:
    group_id = message.chat.id

    # First, react to the bot itself being added to a group.
    for user in message.new_chat_members:
        if user.id == BOT_ID:
            if db.try_claim_group(OWNER_ID, group_id):
                db.set_bot_group(OWNER_ID, group_id)
                _announce_group_ready(group_id)
            else:
                try:
                    bot.send_message(
                        group_id,
                        "⚠️ این ربات فقط برای یک گروه دیگر تنظیم شده است. در حال خروج...",
                    )
                    bot.leave_chat(group_id)
                except Exception as exc:
                    log.error("Error leaving extra group: %s", exc)
            return

    if not db.is_allowed_group(OWNER_ID, group_id):
        return

    for user in message.new_chat_members:
        if user.id == BOT_ID or user.is_bot:
            continue
        _challenge_member(group_id, user)


def _announce_group_ready(group_id: int) -> None:
    try:
        admins = bot.get_chat_administrators(group_id)
        owner_is_admin = any(a.user.id == OWNER_ID for a in admins)
    except Exception as exc:
        log.error("Error checking admins: %s", exc)
        owner_is_admin = False

    if owner_is_admin:
        text = (
            "✅ این گروه به عنوان گروه مجاز تنظیم شد!\n\n"
            "🤖 ربات آماده است. اعضای جدید باید کپچا را حل کنند.\n\n"
            f"📝 ID گروه: {group_id}"
        )
    else:
        text = (
            "⚠️ برای تنظیم نهایی، مالک ربات (شما) باید ادمین این گروه باشید.\n\n"
            "✅ گروه به صورت موقت تنظیم شد.\n\n"
            f"📝 ID گروه: {group_id}"
        )
    try:
        bot.send_message(group_id, text)
    except Exception as exc:
        log.error("Error announcing group ready: %s", exc)


def _challenge_member(group_id: int, user) -> None:
    user_id = user.id
    try:
        if db.is_verified(user_id, group_id):
            try:
                _unmute(group_id, user_id)
                bot.send_message(
                    group_id,
                    f"خوش آمدید {user.first_name}! ✅ شما قبلاً تایید هویت کرده‌اید.",
                )
            except Exception as exc:
                log.error("Error unmuting verified user: %s", exc)
            return

        row = db.get_failed(user_id, group_id)
        fails = row["fails"] if row else 0
        ban_until = row["ban_until"] if row else None
        now = time.time()

        if ban_until and now < ban_until:
            _safe_ban(group_id, user_id, until=int(ban_until))
            return
        if fails >= config.PERMANENT_BAN_AT_FAILS:
            _safe_ban(group_id, user_id)
            return

        db.ensure_failed_row(user_id, group_id)

        try:
            bot.restrict_chat_member(
                group_id, user_id, permissions=MUTED_PERMISSIONS,
                use_independent_chat_permissions=True,
            )
        except Exception as exc:
            log.error("Error restricting user: %s", exc)

        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                text="✅ تایید هویت",
                url=f"https://t.me/{BOT_USERNAME}?start=verify_{group_id}_{user_id}",
            )
        )
        try:
            bot.send_message(
                group_id,
                f"خوش آمدید {user.first_name}! 👋\n\n"
                "برای ارسال پیام در گروه، لطفاً روی دکمه زیر کلیک کنید و کپچا را حل کنید.",
                reply_markup=markup,
            )
        except Exception as exc:
            log.error("Error sending welcome message: %s", exc)
    except Exception as exc:
        log.error("Error challenging member: %s", exc)


@bot.message_handler(content_types=["left_chat_member"])
def handle_left_member(message) -> None:
    try:
        db.forget_member(message.left_chat_member.id, message.chat.id)
        db.clear_pending(OWNER_ID, message.left_chat_member.id)
    except Exception as exc:
        log.error("Error in handle_left_member: %s", exc)


def _safe_ban(group_id: int, user_id: int, until: int | None = None) -> None:
    try:
        if until is not None:
            bot.ban_chat_member(group_id, user_id, until_date=until)
        else:
            bot.ban_chat_member(group_id, user_id)
    except Exception as exc:
        log.error("Error banning user: %s", exc)


# --------------------------------------------------------------------------- #
# Private chat: verification flow
# --------------------------------------------------------------------------- #
@bot.message_handler(commands=["start"])
def handle_start(message) -> None:
    if message.chat.type != "private":
        return

    args = message.text.split(" ", 1)
    if len(args) != 2 or not args[1].startswith("verify_"):
        bot.send_message(
            message.chat.id,
            "👋 خوش آمدید! این ربات انتی اسپم برای محافظت از گروه شماست.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    parts = args[1].split("_")
    if len(parts) != 3:
        bot.send_message(message.chat.id, "❌ لینک تایید هویت نامعتبر است.")
        return

    try:
        group_id = int(parts[1])
        user_id = int(parts[2])
    except ValueError:
        bot.send_message(message.chat.id, "❌ لینک تایید هویت نامعتبر است.")
        return

    if user_id != message.from_user.id:
        bot.send_message(message.chat.id, "❌ این تایید هویت برای شما نیست.")
        return

    try:
        if db.is_verified(user_id, group_id):
            bot.send_message(
                message.chat.id,
                "✅ شما قبلاً تایید هویت کرده‌اید. دسترسی شما در گروه فعال است.",
            )
            return

        row = db.get_failed(user_id, group_id)
        fails = row["fails"] if row else 0
        ban_until = row["ban_until"] if row else None
        now = time.time()

        if ban_until and now < ban_until:
            remaining = int((ban_until - now) / 60)
            bot.send_message(
                message.chat.id,
                f"⏳ شما برای {remaining} دقیقه مسدود هستید. لطفاً بعداً تلاش کنید.",
            )
            return
        if fails >= config.PERMANENT_BAN_AT_FAILS:
            bot.send_message(message.chat.id, "🚫 شما به طور دائم از گروه مسدود شده‌اید.")
            return

        code = captcha.new_code()
        buf = captcha.generate_image(code)
        if buf is None:
            bot.send_message(message.chat.id, "❌ خطا در ایجاد کپچا. لطفاً دوباره تلاش کنید.")
            return

        bot.send_photo(
            message.chat.id,
            photo=buf,
            caption="🔐 لطفاً کد نمایش داده شده در تصویر را وارد کنید "
            "(حساس به حروف بزرگ و کوچک):\n\n⬇️ کد را در پیام بعدی ارسال کنید:",
        )
        db.set_pending(OWNER_ID, user_id, group_id, code)
    except Exception as exc:
        log.error("Error in verification start: %s", exc)
        bot.send_message(message.chat.id, "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")


@bot.message_handler(commands=["setgroup"])
def handle_setgroup(message) -> None:
    if message.chat.type == "private":
        bot.send_message(message.chat.id, "❌ این دستور فقط در گروه کار می‌کند.")
        return
    if message.from_user.id != OWNER_ID:
        bot.send_message(message.chat.id, "❌ فقط مالک ربات می‌تواند گروه را تنظیم کند.")
        return

    group_id = message.chat.id
    # Owner explicitly (re)assigns the group, overriding any previous claim.
    with db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO allowed_group (owner_id, group_id) VALUES (%s, %s)
            ON CONFLICT (owner_id) DO UPDATE SET group_id = EXCLUDED.group_id
            """,
            (OWNER_ID, group_id),
        )
    db.set_bot_group(OWNER_ID, group_id)
    bot.send_message(
        message.chat.id,
        f"✅ این گروه به عنوان گروه مجاز تنظیم شد!\n\n📝 ID گروه: {group_id}",
    )


@bot.message_handler(
    func=lambda m: m.chat.type == "private" and m.text and not m.text.startswith("/")
)
def handle_pm_answer(message) -> None:
    user_id = message.from_user.id
    user_answer = message.text.strip()

    try:
        pending = db.get_pending(OWNER_ID, user_id)
        if not pending:
            bot.send_message(
                message.chat.id,
                "❌ تایید هویت در انتظاری وجود ندارد. لطفاً ابتدا روی دکمه تایید هویت در گروه کلیک کنید.",
            )
            return

        group_id, correct_answer = pending

        if user_answer == correct_answer:
            _on_correct_answer(message, user_id, group_id)
        else:
            _on_wrong_answer(message, user_id, group_id)
    except Exception as exc:
        log.error("Error in handle_pm_answer: %s", exc)
        bot.send_message(message.chat.id, "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")


def _on_correct_answer(message, user_id: int, group_id: int) -> None:
    try:
        _unmute(group_id, user_id)
    except Exception as exc:
        log.error("Error unmuting user: %s", exc)
        bot.send_message(
            message.chat.id,
            "❌ خطا در بازگردانی دسترسی. لطفاً به ادمین گروه اطلاع دهید.",
        )
        return

    db.mark_verified(user_id, group_id, time.time())
    db.reset_failed(user_id, group_id)
    db.clear_pending(OWNER_ID, user_id)
    bot.send_message(message.chat.id, "✅ صحیح! دسترسی شما در گروه بازگردانی شد.")


def _on_wrong_answer(message, user_id: int, group_id: int) -> None:
    fails = db.increment_fails(user_id, group_id)

    if fails >= config.PERMANENT_BAN_AT_FAILS:
        _safe_ban(group_id, user_id)
        db.clear_pending(OWNER_ID, user_id)
        bot.send_message(
            message.chat.id, "🚫 تلاش‌های اشتباه زیاد. شما به طور دائم مسدود شدید."
        )
    elif fails >= config.TEMP_BAN_AT_FAILS:
        ban_until = time.time() + config.TEMP_BAN_DURATION_SECONDS
        db.set_ban_until(user_id, group_id, ban_until)
        _safe_ban(group_id, user_id, until=int(ban_until))
        db.clear_pending(OWNER_ID, user_id)
        bot.send_message(
            message.chat.id, "⏳ تلاش‌های اشتباه زیاد. شما برای 1 ساعت مسدود شدید."
        )
    else:
        remaining = config.PERMANENT_BAN_AT_FAILS - fails
        bot.send_message(
            message.chat.id,
            f"❌ پاسخ اشتباه. {remaining} تلاش باقی مانده. لطفاً کد صحیح را ارسال کنید.",
        )


def main() -> None:
    db.init_schema()
    init_identity()
    log.info("Anti-spam bot started for owner %s", OWNER_ID)
    bot.infinity_polling(timeout=60, long_polling_timeout=60)


if __name__ == "__main__":
    main()
