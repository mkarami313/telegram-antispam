import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions, ReplyKeyboardRemove
import random
import time
import io
import string
from PIL import Image, ImageDraw, ImageFont
import sqlite3
import threading
import subprocess
import os
import json

MAIN_BOT_TOKEN = 'YOUR_MAIN_BOT_TOKEN_HERE'
main_bot = telebot.TeleBot(MAIN_BOT_TOKEN)

BOTS_DIR = 'created_bots'
BOTS_DB = 'bots_manager.db'

if not os.path.exists(BOTS_DIR):
    os.makedirs(BOTS_DIR)

conn_manager = sqlite3.connect(BOTS_DB, check_same_thread=False)
cursor_manager = conn_manager.cursor()

cursor_manager.execute('''
CREATE TABLE IF NOT EXISTS user_bots (
    user_id INTEGER PRIMARY KEY,
    bot_token TEXT,
    bot_username TEXT,
    group_id INTEGER,
    created_at REAL,
    process_id INTEGER
)
''')
conn_manager.commit()

user_states = {}

def generate_bot_code(bot_token, owner_id):
    code = f'''import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions, ReplyKeyboardRemove
import random
import time
import io
import string
from PIL import Image, ImageDraw, ImageFont
import sqlite3
import threading

BOT_TOKEN = '{bot_token}'
OWNER_ID = {owner_id}
bot = telebot.TeleBot(BOT_TOKEN)

DB_FILE = 'bot_{owner_id}.db'

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    return conn

conn = get_db_connection()
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS failed_attempts (
    user_id INTEGER,
    group_id INTEGER,
    fails INTEGER DEFAULT 0,
    ban_until REAL,
    PRIMARY KEY (user_id, group_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS pending_verifications (
    user_id INTEGER,
    group_id INTEGER,
    answer TEXT,
    timestamp REAL,
    PRIMARY KEY (user_id, group_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS verified_users (
    user_id INTEGER,
    group_id INTEGER,
    verified_at REAL,
    PRIMARY KEY (user_id, group_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS allowed_group (
    group_id INTEGER PRIMARY KEY
)
""")

conn.commit()

BOT_ID = None

def init_bot_id():
    global BOT_ID
    try:
        BOT_ID = bot.get_me().id
    except Exception as e:
        print(f"Error getting bot ID: {{e}}")
        time.sleep(5)
        init_bot_id()

init_bot_id()

def is_allowed_group(group_id):
    conn_local = get_db_connection()
    cursor_local = conn_local.cursor()
    cursor_local.execute('SELECT group_id FROM allowed_group WHERE group_id = ?', (group_id,))
    result = cursor_local.fetchone()
    conn_local.close()
    return result is not None

def has_any_allowed_group():
    conn_local = get_db_connection()
    cursor_local = conn_local.cursor()
    cursor_local.execute('SELECT COUNT(*) FROM allowed_group')
    count = cursor_local.fetchone()[0]
    conn_local.close()
    return count > 0

def set_allowed_group(group_id):
    conn_local = get_db_connection()
    cursor_local = conn_local.cursor()
    cursor_local.execute('DELETE FROM allowed_group')
    cursor_local.execute('INSERT INTO allowed_group (group_id) VALUES (?)', (group_id,))
    conn_local.commit()
    conn_local.close()

def generate_captcha_image(code):
    try:
        font_size = 40
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except:
            font = ImageFont.load_default()
        
        char_spacing = 35
        width = len(code) * char_spacing + 40
        height = font_size + 40
        image = Image.new('RGB', (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        for i, char in enumerate(code):
            char_image = Image.new('RGBA', (char_spacing + 20, height + 20), (0, 0, 0, 0))
            char_draw = ImageDraw.Draw(char_image)
            char_draw.text((5, 5), char, font=font, fill=(0, 0, 0, 255))
            angle = random.randint(-25, 25)
            rotated = char_image.rotate(angle, expand=True, resample=Image.BICUBIC)
            paste_x = 15 + i * (char_spacing - 3)
            paste_y = random.randint(5, 15)
            image.paste(rotated, (paste_x, paste_y), rotated)

        for _ in range(6):
            x1 = random.randint(0, width)
            y1 = random.randint(0, height)
            x2 = random.randint(0, width)
            y2 = random.randint(0, height)
            draw.line((x1, y1, x2, y2), fill=(150, 150, 150), width=2)

        for _ in range(80):
            x = random.randint(0, width - 1)
            y = random.randint(0, height - 1)
            draw.point((x, y), fill=(0, 0, 0))

        buf = io.BytesIO()
        image.save(buf, 'PNG')
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Error generating captcha: {{e}}")
        return None

@bot.message_handler(content_types=['new_chat_members'])
def handle_new_member(message):
    group_id = message.chat.id
    
    for user in message.new_chat_members:
        if user.id == BOT_ID:
            if not has_any_allowed_group():
                set_allowed_group(group_id)
                try:
                    admins = bot.get_chat_administrators(group_id)
                    admin_ids = [admin.user.id for admin in admins]
                    
                    if OWNER_ID in admin_ids:
                        bot.send_message(
                            group_id,
                            f"✅ این گروه به عنوان گروه مجاز تنظیم شد!\\n\\n"
                            f"🤖 ربات آماده است. اعضای جدید باید کپچا را حل کنند.\\n\\n"
                            f"📝 ID گروه: {{group_id}}"
                        )
                    else:
                        bot.send_message(
                            group_id,
                            f"⚠️ برای تنظیم نهایی، مالک ربات (شما) باید ادمین این گروه باشید.\\n\\n"
                            f"✅ گروه به صورت موقت تنظیم شد.\\n\\n"
                            f"📝 ID گروه: {{group_id}}"
                        )
                except Exception as e:
                    print(f"Error checking admins: {{e}}")
                    bot.send_message(
                        group_id,
                        f"✅ این گروه به عنوان گروه مجاز تنظیم شد!\\n\\n"
                        f"📝 ID گروه: {{group_id}}"
                    )
                return
            elif is_allowed_group(group_id):
                bot.send_message(group_id, "✅ ربات آماده است! اعضای جدید باید کپچا را حل کنند.")
                return
            else:
                try:
                    bot.send_message(group_id, "⚠️ این ربات فقط برای یک گروه دیگر تنظیم شده است. در حال خروج...")
                    bot.leave_chat(group_id)
                except:
                    pass
                return
    
    if not is_allowed_group(group_id):
        return
    
    for user in message.new_chat_members:
        user_id = user.id
        if user_id == BOT_ID or user.is_bot:
            continue
        
        try:
            conn_local = get_db_connection()
            cursor_local = conn_local.cursor()
            
            cursor_local.execute('SELECT verified_at FROM verified_users WHERE user_id = ? AND group_id = ?', (user_id, group_id))
            verified_row = cursor_local.fetchone()
            
            if verified_row:
                try:
                    bot.restrict_chat_member(
                        group_id,
                        user_id,
                        permissions=ChatPermissions(
                            can_send_messages=True,
                            can_send_media_messages=True,
                            can_send_polls=True,
                            can_send_other_messages=True,
                            can_add_web_page_previews=True,
                            can_invite_users=True,
                            can_pin_messages=True
                        ),
                        use_independent_chat_permissions=True
                    )
                    bot.send_message(group_id, f"خوش آمدید {{user.first_name}}! ✅ شما قبلاً تایید هویت کرده‌اید.")
                except Exception as e:
                    print(f"Error unmuting verified user: {{e}}")
                conn_local.close()
                continue
            
            cursor_local.execute('SELECT fails, ban_until FROM failed_attempts WHERE user_id = ? AND group_id = ?', (user_id, group_id))
            row = cursor_local.fetchone()
            fails = row[0] if row else 0
            ban_until = row[1] if row else None
            current_time = time.time()
            
            if ban_until and current_time < ban_until:
                try:
                    bot.ban_chat_member(group_id, user_id, until_date=int(ban_until))
                except Exception as e:
                    print(f"Error banning user: {{e}}")
                conn_local.close()
                continue
            
            if fails >= 5:
                try:
                    bot.ban_chat_member(group_id, user_id)
                except Exception as e:
                    print(f"Error banning user permanently: {{e}}")
                conn_local.close()
                continue
            
            if not row:
                cursor_local.execute('INSERT INTO failed_attempts (user_id, group_id, fails, ban_until) VALUES (?, ?, 0, NULL)', (user_id, group_id))
                conn_local.commit()
            
            try:
                bot.restrict_chat_member(
                    group_id,
                    user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    use_independent_chat_permissions=True
                )
            except Exception as e:
                print(f"Error restricting user: {{e}}")
            
            markup = InlineKeyboardMarkup()
            bot_username = bot.get_me().username
            verify_button = InlineKeyboardButton(
                text="✅ تایید هویت",
                url=f"https://t.me/{{bot_username}}?start=verify_{{group_id}}_{{user_id}}"
            )
            markup.add(verify_button)
            
            try:
                bot.send_message(
                    group_id,
                    f"خوش آمدید {{user.first_name}}! 👋\\n\\nبرای ارسال پیام در گروه، لطفاً روی دکمه زیر کلیک کنید و کپچا را حل کنید.",
                    reply_markup=markup
                )
            except Exception as e:
                print(f"Error sending welcome message: {{e}}")
            
            conn_local.close()
        except Exception as e:
            print(f"Error in handle_new_member: {{e}}")

@bot.message_handler(content_types=['left_chat_member'])
def handle_left_member(message):
    group_id = message.chat.id
    user_id = message.left_chat_member.id
    
    try:
        conn_local = get_db_connection()
        cursor_local = conn_local.cursor()
        
        cursor_local.execute('DELETE FROM verified_users WHERE user_id = ? AND group_id = ?', (user_id, group_id))
        cursor_local.execute('DELETE FROM pending_verifications WHERE user_id = ? AND group_id = ?', (user_id, group_id))
        conn_local.commit()
        conn_local.close()
    except Exception as e:
        print(f"Error in handle_left_member: {{e}}")

@bot.message_handler(commands=['start'])
def handle_start(message):
    if message.chat.type != 'private':
        return
    
    args = message.text.split(' ', 1)
    if len(args) == 2 and args[1].startswith('verify_'):
        parts = args[1].split('_')
        if len(parts) == 3:
            try:
                group_id = int(parts[1])
                user_id = int(parts[2])
                
                if user_id != message.from_user.id:
                    bot.send_message(message.chat.id, "❌ این تایید هویت برای شما نیست.", reply_markup=ReplyKeyboardRemove())
                    return
                
                conn_local = get_db_connection()
                cursor_local = conn_local.cursor()
                
                cursor_local.execute('SELECT verified_at FROM verified_users WHERE user_id = ? AND group_id = ?', (user_id, group_id))
                verified_row = cursor_local.fetchone()
                
                if verified_row:
                    bot.send_message(message.chat.id, "✅ شما قبلاً تایید هویت کرده‌اید. دسترسی شما در گروه فعال است.", reply_markup=ReplyKeyboardRemove())
                    conn_local.close()
                    return
                
                cursor_local.execute('SELECT fails, ban_until FROM failed_attempts WHERE user_id = ? AND group_id = ?', (user_id, group_id))
                row = cursor_local.fetchone()
                fails = row[0] if row else 0
                ban_until = row[1] if row else None
                current_time = time.time()
                
                if ban_until and current_time < ban_until:
                    remaining = int((ban_until - current_time) / 60)
                    bot.send_message(message.chat.id, f"⏳ شما برای {{remaining}} دقیقه مسدود هستید. لطفاً بعداً تلاش کنید.", reply_markup=ReplyKeyboardRemove())
                    conn_local.close()
                    return
                
                if fails >= 5:
                    bot.send_message(message.chat.id, "🚫 شما به طور دائم از گروه مسدود شده‌اید.", reply_markup=ReplyKeyboardRemove())
                    conn_local.close()
                    return
                
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                buf = generate_captcha_image(code)
                
                if buf is None:
                    bot.send_message(message.chat.id, "❌ خطا در ایجاد کپچا. لطفاً دوباره تلاش کنید.", reply_markup=ReplyKeyboardRemove())
                    conn_local.close()
                    return
                
                bot.send_photo(
                    message.chat.id, 
                    photo=buf, 
                    caption="🔐 لطفاً کد نمایش داده شده در تصویر را وارد کنید (حساس به حروف بزرگ و کوچک):\\n\\n⬇️ کد را در پیام بعدی ارسال کنید:",
                    reply_markup=ReplyKeyboardRemove()
                )
                
                cursor_local.execute("""
                INSERT OR REPLACE INTO pending_verifications (user_id, group_id, answer, timestamp)
                VALUES (?, ?, ?, ?)
                """, (user_id, group_id, code, time.time()))
                conn_local.commit()
                conn_local.close()
                
            except ValueError:
                bot.send_message(message.chat.id, "❌ لینک تایید هویت نامعتبر است.", reply_markup=ReplyKeyboardRemove())
            except Exception as e:
                print(f"Error in verification start: {{e}}")
                bot.send_message(message.chat.id, "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.", reply_markup=ReplyKeyboardRemove())
        else:
            bot.send_message(message.chat.id, "❌ لینک تایید هویت نامعتبر است.", reply_markup=ReplyKeyboardRemove())
    else:
        bot.send_message(message.chat.id, "👋 خوش آمدید! این ربات انتی اسپم برای محافظت از گروه شماست.", reply_markup=ReplyKeyboardRemove())

@bot.message_handler(commands=['setgroup'])
def handle_setgroup(message):
    if message.chat.type == 'private':
        bot.send_message(message.chat.id, "❌ این دستور فقط در گروه کار می‌کند.")
        return
    
    if message.from_user.id != OWNER_ID:
        bot.send_message(message.chat.id, "❌ فقط مالک ربات می‌تواند گروه را تنظیم کند.")
        return
    
    group_id = message.chat.id
    set_allowed_group(group_id)
    bot.send_message(message.chat.id, f"✅ این گروه به عنوان گروه مجاز تنظیم شد!\\n\\n📝 ID گروه: {{group_id}}")

@bot.message_handler(func=lambda message: message.chat.type == 'private' and message.text and not message.text.startswith('/'))
def handle_pm_answer(message):
    user_id = message.from_user.id
    user_answer = message.text.strip()
    
    try:
        conn_local = get_db_connection()
        cursor_local = conn_local.cursor()
        
        cursor_local.execute('SELECT group_id, answer, timestamp FROM pending_verifications WHERE user_id = ?', (user_id,))
        row = cursor_local.fetchone()
        
        if row:
            group_id, correct_answer, timestamp = row
            
            if user_answer == correct_answer:
                try:
                    bot.restrict_chat_member(
                        group_id,
                        user_id,
                        permissions=ChatPermissions(
                            can_send_messages=True,
                            can_send_media_messages=True,
                            can_send_polls=True,
                            can_send_other_messages=True,
                            can_add_web_page_previews=True,
                            can_invite_users=True,
                            can_pin_messages=True
                        ),
                        use_independent_chat_permissions=True
                    )
                    
                    cursor_local.execute('INSERT OR REPLACE INTO verified_users (user_id, group_id, verified_at) VALUES (?, ?, ?)', 
                                       (user_id, group_id, time.time()))
                    cursor_local.execute('DELETE FROM failed_attempts WHERE user_id = ? AND group_id = ?', (user_id, group_id))
                    cursor_local.execute('DELETE FROM pending_verifications WHERE user_id = ? AND group_id = ?', (user_id, group_id))
                    conn_local.commit()
                    
                    bot.send_message(message.chat.id, "✅ صحیح! دسترسی شما در گروه بازگردانی شد.", reply_markup=ReplyKeyboardRemove())
                except Exception as e:
                    print(f"Error unmuting user: {{e}}")
                    bot.send_message(message.chat.id, "❌ خطا در بازگردانی دسترسی. لطفاً به ادمین گروه اطلاع دهید.", reply_markup=ReplyKeyboardRemove())
            else:
                cursor_local.execute('UPDATE failed_attempts SET fails = fails + 1 WHERE user_id = ? AND group_id = ?', (user_id, group_id))
                conn_local.commit()
                
                cursor_local.execute('SELECT fails FROM failed_attempts WHERE user_id = ? AND group_id = ?', (user_id, group_id))
                row = cursor_local.fetchone()
                fails = row[0] if row else 0
                
                if fails == 3:
                    ban_until = time.time() + 3600
                    cursor_local.execute('UPDATE failed_attempts SET ban_until = ? WHERE user_id = ? AND group_id = ?', (ban_until, user_id, group_id))
                    conn_local.commit()
                    try:
                        bot.ban_chat_member(group_id, user_id, until_date=int(ban_until))
                    except Exception as e:
                        print(f"Error banning user: {{e}}")
                    bot.send_message(message.chat.id, "⏳ تلاش‌های اشتباه زیاد. شما برای 1 ساعت مسدود شدید.", reply_markup=ReplyKeyboardRemove())
                    cursor_local.execute('DELETE FROM pending_verifications WHERE user_id = ? AND group_id = ?', (user_id, group_id))
                    conn_local.commit()
                elif fails >= 5:
                    try:
                        bot.ban_chat_member(group_id, user_id)
                    except Exception as e:
                        print(f"Error banning user permanently: {{e}}")
                    bot.send_message(message.chat.id, "🚫 تلاش‌های اشتباه زیاد. شما به طور دائم مسدود شدید.", reply_markup=ReplyKeyboardRemove())
                    cursor_local.execute('DELETE FROM pending_verifications WHERE user_id = ? AND group_id = ?', (user_id, group_id))
                    conn_local.commit()
                else:
                    remaining = 5 - fails
                    bot.send_message(message.chat.id, f"❌ پاسخ اشتباه. {{remaining}} تلاش باقی مانده. لطفاً کد صحیح را ارسال کنید.", reply_markup=ReplyKeyboardRemove())
        else:
            bot.send_message(message.chat.id, "❌ تایید هویت در انتظاری وجود ندارد. لطفاً ابتدا روی دکمه تایید هویت در گروه کلیک کنید.", reply_markup=ReplyKeyboardRemove())
        
        conn_local.close()
    except Exception as e:
        print(f"Error in handle_pm_answer: {{e}}")
        bot.send_message(message.chat.id, "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.", reply_markup=ReplyKeyboardRemove())

def cleanup_verifications():
    while True:
        try:
            time.sleep(300)
            conn_local = get_db_connection()
            cursor_local = conn_local.cursor()
            current_time = time.time()
            cursor_local.execute('DELETE FROM pending_verifications WHERE timestamp < ?', (current_time - 3600,))
            conn_local.commit()
            conn_local.close()
        except Exception as e:
            print(f"Error in cleanup: {{e}}")

cleanup_thread = threading.Thread(target=cleanup_verifications, daemon=True)
cleanup_thread.start()

if __name__ == '__main__':
    print(f"Bot started for user {{OWNER_ID}}...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
'''
    return code

def validate_bot_token(token):
    try:
        test_bot = telebot.TeleBot(token)
        bot_info = test_bot.get_me()
        return True, bot_info.username
    except Exception as e:
        return False, str(e)

@main_bot.message_handler(commands=['start'])
def handle_main_start(message):
    user_id = message.from_user.id
    
    cursor_manager.execute('SELECT bot_token, bot_username FROM user_bots WHERE user_id = ?', (user_id,))
    existing_bot = cursor_manager.fetchone()
    
    if existing_bot:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🗑 حذف ربات", callback_data="delete_bot"))
        markup.add(InlineKeyboardButton("ℹ️ اطلاعات ربات", callback_data="bot_info"))
        main_bot.send_message(
            message.chat.id,
            f"👋 خوش آمدید!\n\n✅ شما قبلاً یک ربات ساخته‌اید:\n@{existing_bot[1]}\n\nبرای ساخت ربات جدید، ابتدا ربات فعلی را حذف کنید.",
            reply_markup=markup
        )
    else:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🤖 ساخت ربات انتی اسپم", callback_data="create_bot"))
        main_bot.send_message(
            message.chat.id,
            "👋 خوش آمدید به ربات ساز انتی اسپم!\n\n🔹 با این ربات می‌توانید یک ربات انتی اسپم اختصاصی برای گروه خود بسازید.\n\n🔸 ویژگی‌ها:\n• سیستم کپچا هوشمند\n• محدودیت تلاش\n• بن موقت و دائم\n• فقط یک گروه مجاز\n\nبرای شروع روی دکمه زیر کلیک کنید:",
            reply_markup=markup
        )

@main_bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    
    if call.data == "create_bot":
        user_states[user_id] = "waiting_token"
        main_bot.edit_message_text(
            "🔑 لطفاً توکن ربات تلگرام خود را ارسال کنید:\n\n⚠️ برای دریافت توکن:\n1. به @BotFather مراجعه کنید\n2. دستور /newbot را ارسال کنید\n3. نام و یوزرنیم ربات را تعیین کنید\n4. توکن دریافتی را اینجا ارسال کنید\n\n❌ برای لغو /cancel را ارسال کنید",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
    
    elif call.data == "delete_bot":
        cursor_manager.execute('SELECT bot_token, bot_username, process_id FROM user_bots WHERE user_id = ?', (user_id,))
        bot_data = cursor_manager.fetchone()
        
        if bot_data:
            if bot_data[2]:
                try:
                    os.kill(bot_data[2], 9)
                except:
                    pass
            
            bot_file = os.path.join(BOTS_DIR, f'bot_{user_id}.py')
            if os.path.exists(bot_file):
                os.remove(bot_file)
            
            db_file = f'bot_{user_id}.db'
            if os.path.exists(db_file):
                os.remove(db_file)
            
            cursor_manager.execute('DELETE FROM user_bots WHERE user_id = ?', (user_id,))
            conn_manager.commit()
            
            main_bot.edit_message_text(
                f"✅ ربات @{bot_data[1]} با موفقیت حذف شد!\n\nبرای ساخت ربات جدید /start را ارسال کنید.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
    
    elif call.data == "bot_info":
        cursor_manager.execute('SELECT bot_username, created_at FROM user_bots WHERE user_id = ?', (user_id,))
        bot_data = cursor_manager.fetchone()
        
        if bot_data:
            created_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(bot_data[1]))
            main_bot.answer_callback_query(
                call.id,
                f"ربات: @{bot_data[0]}\nتاریخ ساخت: {created_time}",
                show_alert=True
            )

@main_bot.message_handler(commands=['cancel'])
def handle_cancel(message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
        main_bot.send_message(message.chat.id, "❌ عملیات لغو شد.\n\nبرای شروع مجدد /start را ارسال کنید.")
    else:
        main_bot.send_message(message.chat.id, "هیچ عملیاتی در انتظار نیست.")

@main_bot.message_handler(func=lambda message: message.from_user.id in user_states)
def handle_user_input(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    
    if state == "waiting_token":
        bot_token = message.text.strip()
        
        cursor_manager.execute('SELECT user_id FROM user_bots WHERE bot_token = ?', (bot_token,))
        if cursor_manager.fetchone():
            main_bot.send_message(message.chat.id, "❌ این توکن قبلاً استفاده شده است!")
            return
        
        msg = main_bot.send_message(message.chat.id, "⏳ در حال بررسی توکن...")
        
        is_valid, bot_username = validate_bot_token(bot_token)
        
        if not is_valid:
            main_bot.edit_message_text(
                f"❌ توکن نامعتبر است!\n\nخطا: {bot_username}\n\nلطفاً توکن صحیح را ارسال کنید یا /cancel برای لغو.",
                chat_id=message.chat.id,
                message_id=msg.message_id
            )
            return
        
        main_bot.edit_message_text(
            "✅ توکن معتبر است!\n⏳ در حال ساخت ربات...",
            chat_id=message.chat.id,
            message_id=msg.message_id
        )
        
        bot_code = generate_bot_code(bot_token, user_id)
        bot_file = os.path.join(BOTS_DIR, f'bot_{user_id}.py')
        
        with open(bot_file, 'w', encoding='utf-8') as f:
            f.write(bot_code)
        
        cursor_manager.execute('''
        INSERT OR REPLACE INTO user_bots (user_id, bot_token, bot_username, created_at)
        VALUES (?, ?, ?, ?)
        ''', (user_id, bot_token, bot_username, time.time()))
        conn_manager.commit()
        
        try:
            process = subprocess.Popen(['python3', bot_file], 
                                     stdout=subprocess.PIPE, 
                                     stderr=subprocess.PIPE)
            
            time.sleep(3)
            
            if process.poll() is None:
                cursor_manager.execute('UPDATE user_bots SET process_id = ? WHERE user_id = ?', (process.pid, user_id))
                conn_manager.commit()
                
                main_bot.edit_message_text(
                    f"✅ ربات شما با موفقیت ساخته شد!\n\n"
                    f"🤖 یوزرنیم ربات: @{bot_username}\n\n"
                    f"📋 مراحل راه‌اندازی:\n"
                    f"1️⃣ ربات را به گروه خود اضافه کنید\n"
                    f"2️⃣ ربات را ادمین کنید (با دسترسی حذف کاربران)\n"
                    f"3️⃣ گروه به صورت خودکار تنظیم می‌شود!\n\n"
                    f"✅ حالا ربات شما آماده است!\n\n"
                    f"⚠️ نکته: ربات فقط در یک گروه کار می‌کند. اگر به گروه دوم اضافه شود، خودکار خارج می‌شود.",
                    chat_id=message.chat.id,
                    message_id=msg.message_id
                )
            else:
                main_bot.edit_message_text(
                    "❌ خطا در راه‌اندازی ربات. لطفاً دوباره تلاش کنید.",
                    chat_id=message.chat.id,
                    message_id=msg.message_id
                )
        except Exception as e:
            main_bot.edit_message_text(
                f"❌ خطا در راه‌اندازی ربات:\n{str(e)}",
                chat_id=message.chat.id,
                message_id=msg.message_id
            )
        
        del user_states[user_id]

@main_bot.message_handler(commands=['mybots'])
def handle_mybots(message):
    user_id = message.from_user.id
    
    cursor_manager.execute('SELECT bot_username, created_at, group_id FROM user_bots WHERE user_id = ?', (user_id,))
    bot_data = cursor_manager.fetchone()
    
    if bot_data:
        created_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(bot_data[1]))
        group_status = f"گروه تنظیم شده: {bot_data[2]}" if bot_data[2] else "گروه تنظیم نشده"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🗑 حذف ربات", callback_data="delete_bot"))
        
        main_bot.send_message(
            message.chat.id,
            f"🤖 ربات شما:\n\n"
            f"👤 یوزرنیم: @{bot_data[0]}\n"
            f"📅 تاریخ ساخت: {created_time}\n"
            f"📊 وضعیت: {group_status}\n\n"
            f"✅ ربات در حال اجراست!",
            reply_markup=markup
        )
    else:
        main_bot.send_message(
            message.chat.id,
            "❌ شما هیچ رباتی ندارید.\n\nبرای ساخت ربات /start را ارسال کنید."
        )

@main_bot.message_handler(commands=['help'])
def handle_help(message):
    help_text = """
📖 راهنمای استفاده:

🔹 دستورات:
/start - شروع و ساخت ربات
/mybots - مشاهده ربات‌های من
/help - نمایش این راهنما

🔹 نحوه استفاده:
1. دستور /start را ارسال کنید
2. روی "ساخت ربات انتی اسپم" کلیک کنید
3. توکن ربات را از @BotFather دریافت کنید
4. توکن را ارسال کنید
5. ربات را به گروه اضافه کنید و ادمین کنید
6. گروه خودکار تنظیم می‌شود!

🔹 ویژگی‌های ربات:
✅ سیستم کپچا تصویری
✅ محدودیت تلاش (5 بار)
✅ بن موقت بعد از 3 تلاش اشتباه (1 ساعت)
✅ بن دائم بعد از 5 تلاش اشتباه
✅ حذف تایید هویت با خروج از گروه
✅ محدودیت یک گروه (تنظیم خودکار)

⚠️ نکات مهم:
• ربات باید ادمین باشد
• دسترسی حذف کاربران الزامی است
• اولین گروهی که ربات به آن اضافه می‌شود، گروه مجاز است
• ربات 24/7 روی سرور اجرا می‌شود

💬 پشتیبانی: @YourSupportUsername
"""
    main_bot.send_message(message.chat.id, help_text)

def restart_all_bots():
    """راه‌اندازی مجدد تمام ربات‌های موجود هنگام استارت"""
    cursor_manager.execute('SELECT user_id, bot_token FROM user_bots')
    all_bots = cursor_manager.fetchall()
    
    for user_id, bot_token in all_bots:
        bot_file = os.path.join(BOTS_DIR, f'bot_{user_id}.py')
        if os.path.exists(bot_file):
            try:
                process = subprocess.Popen(['python3', bot_file], 
                                         stdout=subprocess.PIPE, 
                                         stderr=subprocess.PIPE)
                cursor_manager.execute('UPDATE user_bots SET process_id = ? WHERE user_id = ?', (process.pid, user_id))
                conn_manager.commit()
                print(f"Bot for user {user_id} started successfully")
            except Exception as e:
                print(f"Error starting bot for user {user_id}: {e}")

def monitor_bots():
    """مانیتورینگ ربات‌ها و راه‌اندازی مجدد در صورت خرابی"""
    while True:
        try:
            time.sleep(60)
            cursor_manager.execute('SELECT user_id, process_id FROM user_bots WHERE process_id IS NOT NULL')
            bots = cursor_manager.fetchall()
            
            for user_id, process_id in bots:
                try:
                    os.kill(process_id, 0)
                except OSError:
                    print(f"Bot for user {user_id} crashed. Restarting...")
                    bot_file = os.path.join(BOTS_DIR, f'bot_{user_id}.py')
                    if os.path.exists(bot_file):
                        try:
                            process = subprocess.Popen(['python3', bot_file], 
                                                     stdout=subprocess.PIPE, 
                                                     stderr=subprocess.PIPE)
                            cursor_manager.execute('UPDATE user_bots SET process_id = ? WHERE user_id = ?', (process.pid, user_id))
                            conn_manager.commit()
                            print(f"Bot for user {user_id} restarted successfully")
                        except Exception as e:
                            print(f"Error restarting bot for user {user_id}: {e}")
        except Exception as e:
            print(f"Error in monitor_bots: {e}")

if __name__ == '__main__':
    print("Main bot started...")
    print("Restarting existing bots...")
    restart_all_bots()
    
    monitor_thread = threading.Thread(target=monitor_bots, daemon=True)
    monitor_thread.start()
    
    print("Bot manager is ready!")
    main_bot.infinity_polling(timeout=60, long_polling_timeout=60)