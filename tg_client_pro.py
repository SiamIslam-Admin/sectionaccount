import os
import random
import sqlite3

from pyrogram import Client, filters, errors
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.raw.functions.payments import GetStarsStatus
from pyrogram.raw.types import InputPeerSelf

# -------------------- CONFIGURATION --------------------
API_ID = 35648548
API_HASH = "7cb954d06d962e181fb1717fe1a486a8"
BOT_TOKEN = "8680453474:AAGUbqhHNisblfhL6UNLokzwwNao74Tp29w"

# ⚠️ SECURITY WARNING:
# You exposed your API_HASH and BOT_TOKEN publicly.
# Rotate them ASAP (BotFather + my.telegram.org).

bot = Client("Account_session_manager_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Database & Directory setup
DB_PATH = "sessions.db"
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# -------------------- DATABASE FUNCTIONS --------------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             user_id INTEGER,
             session_id TEXT UNIQUE,
             phone TEXT,
             first_name TEXT,
             last_name TEXT,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )

def add_session_to_db(user_id, session_id, phone, fn, ln):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (user_id, session_id, phone, first_name, last_name) VALUES (?, ?, ?, ?, ?)",
            (user_id, session_id, phone, fn, ln),
        )

def get_user_sessions(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT session_id, phone, first_name, last_name FROM sessions WHERE user_id = ?",
            (user_id,),
        ).fetchall()

def get_session_owner(session_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else None

def delete_session_from_db(user_id: int, session_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )

init_db()

# -------------------- HELPERS --------------------
def normalize_otp(text: str) -> str:
    """
    User may type: 27282 OR 2 7 2 8 2 OR 2-7-2-8-2
    We normalize to: '2 7 2 8 2'
    """
    digits = "".join(ch for ch in text if ch.isdigit())
    return " ".join(digits)

def session_file_paths(session_id: str):
    # Pyrogram sqlite session filename is usually <name>.session
    main = os.path.join(SESSIONS_DIR, f"{session_id}.session")
    journal = os.path.join(SESSIONS_DIR, f"{session_id}.session-journal")
    return [main, journal]

def delete_session_files(session_id: str):
    for p in session_file_paths(session_id):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

# -------------------- USER STATE STORAGE --------------------
# user_id: {step, phone, hash, client, session_id}
user_states = {}

# -------------------- HANDLERS --------------------
@bot.on_message(filters.command("start") & filters.private)
async def start(client, message: Message):
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add New Account", callback_data="add_session")],
            [InlineKeyboardButton("📋 My Accounts", callback_data="my_sessions")],
        ]
    )
    await message.reply(
        "📱 **Telegram Session & Star Manager**\nআপনার অ্যাকাউন্ট ম্যানেজ করুন সহজেই।",
        reply_markup=keyboard,
    )

@bot.on_callback_query()
async def handle_callback(client, callback_query: CallbackQuery):
    u_id = callback_query.from_user.id
    data = callback_query.data

    if data == "add_session":
        user_states[u_id] = {"step": "phone"}
        await callback_query.message.edit_text("📞 আপনার ফোন নম্বরটি দিন (Example: +88017xxx):")

    elif data == "my_sessions":
        sessions = get_user_sessions(u_id)
        if not sessions:
            return await callback_query.answer("কোন অ্যাকাউন্ট পাওয়া যায়নি!", show_alert=True)

        buttons = [
            [InlineKeyboardButton(f"👤 {s[2] or s[1]}", callback_data=f"view|{s[0]}")]
            for s in sessions
        ]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
        await callback_query.message.edit_text(
            "📂 আপনার সংরক্ষিত অ্যাকাউন্টসমূহ:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("view|"):
        s_id = data.split("|", 1)[1]
        await show_session_details(callback_query.message, s_id, u_id)

    elif data.startswith("logout|"):
        # Ask confirmation
        s_id = data.split("|", 1)[1]
        owner = get_session_owner(s_id)
        if owner != u_id:
            return await callback_query.answer("❌ You can't logout this session.", show_alert=True)

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Yes, Logout", callback_data=f"logout_confirm|{s_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"view|{s_id}"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="my_sessions")],
            ]
        )
        await callback_query.message.edit_text(
            "⚠️ আপনি কি নিশ্চিত এই অ্যাকাউন্ট থেকে **Logout** করতে চান?\n\nLogout করলে সেশন ডিলিট হয়ে যাবে।",
            reply_markup=kb,
        )

    elif data.startswith("logout_confirm|"):
        s_id = data.split("|", 1)[1]
        owner = get_session_owner(s_id)
        if owner != u_id:
            return await callback_query.answer("❌ You can't logout this session.", show_alert=True)

        await callback_query.message.edit_text("⏳ Wait a moment... Logging out...")

        temp_client = Client(s_id, api_id=API_ID, api_hash=API_HASH, workdir=SESSIONS_DIR)

        try:
            await temp_client.connect()

            # ✅ This terminates the session internally.
            await temp_client.log_out()

        except Exception as e:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="my_sessions")]])

            # Try disconnect only if possible (ignore terminate errors)
            try:
                if getattr(temp_client, "is_connected", False):
                    await temp_client.disconnect()
            except Exception:
                pass

            return await callback_query.message.edit_text(f"❌ Logout failed: {e}", reply_markup=kb)

        # ✅ After log_out() do NOT disconnect (it can be "already terminated")

        # Remove from DB + delete local session files
        delete_session_from_db(u_id, s_id)
        delete_session_files(s_id)

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 My Accounts", callback_data="my_sessions")]])
        await callback_query.message.edit_text("✅ Logout done! সেশন ডিলিট করা হয়েছে।", reply_markup=kb)

    elif data == "back":
        await start(client, callback_query.message)

# -------------------- LOGIN LOGIC (OTP & 2FA) --------------------
@bot.on_message(filters.private & filters.text)
async def login_flow(client, message: Message):
    u_id = message.from_user.id
    if u_id not in user_states:
        return

    state = user_states[u_id]
    step = state.get("step")

    # STEP 1: Phone Number
    if step == "phone":
        phone = message.text.strip()
        session_name = f"sess_{u_id}_{random.randint(1000, 9999)}"
        temp_client = Client(session_name, api_id=API_ID, api_hash=API_HASH, workdir=SESSIONS_DIR)

        wait_msg = await message.reply("⏳ Wait a moment...")

        await temp_client.connect()
        try:
            code_info = await temp_client.send_code(phone)
            user_states[u_id].update(
                {
                    "step": "otp",
                    "phone": phone,
                    "hash": code_info.phone_code_hash,
                    "client": temp_client,
                    "session_id": session_name,
                }
            )
            await wait_msg.edit_text("📩 আপনার টেলিগ্রামে একটি **OTP** পাঠানো হয়েছে। সেটি এখানে লিখুন:")
        except Exception as e:
            await wait_msg.edit_text(f"❌ ভুল নম্বর বা সমস্যা: {e}")
            try:
                await temp_client.disconnect()
            except Exception:
                pass
            user_states.pop(u_id, None)

    # STEP 2: OTP
    elif step == "otp":
        otp_input = message.text.strip()
        otp = normalize_otp(otp_input)

        temp_client = state["client"]
        wait_msg = await message.reply("⏳ Wait a moment...")

        try:
            await temp_client.sign_in(state["phone"], state["hash"], otp)
            await finalize_login(wait_msg, u_id)
        except errors.SessionPasswordNeeded:
            user_states[u_id]["step"] = "2fa"
            await wait_msg.edit_text("🔐 আপনার অ্যাকাউন্টে **Two-Step Verification** অন করা। পাসওয়ার্ড দিন:")
        except Exception as e:
            await wait_msg.edit_text(f"❌ OTP ভুল: {e}")

    # STEP 3: 2FA Password
    elif step == "2fa":
        password = message.text.strip()
        temp_client = state["client"]
        wait_msg = await message.reply("⏳ Wait a moment...")

        try:
            await temp_client.check_password(password)
            await finalize_login(wait_msg, u_id)
        except Exception as e:
            await wait_msg.edit_text(f"❌ ভুল পাসওয়ার্ড: {e}")

async def finalize_login(status_message: Message, u_id: int):
    state = user_states.get(u_id)
    if not state:
        return

    temp_client = state["client"]
    try:
        me = await temp_client.get_me()
        add_session_to_db(u_id, state["session_id"], state["phone"], me.first_name, me.last_name)
        await status_message.edit_text(f"✅ লগইন সফল! **{me.first_name}** অ্যাকাউন্টটি যুক্ত হয়েছে।")
    finally:
        try:
            await temp_client.disconnect()
        except Exception:
            pass
        user_states.pop(u_id, None)

# -------------------- SHOW DETAILS & STAR BALANCE --------------------
async def show_session_details(message: Message, s_id: str, u_id: int):
    await message.edit_text("⏳ Star balance চেক করা হচ্ছে...")

    owner = get_session_owner(s_id)
    if owner != u_id:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="my_sessions")]])
        return await message.edit_text("❌ আপনি এই সেশন দেখতে পারবেন না।", reply_markup=kb)

    temp_client = Client(s_id, api_id=API_ID, api_hash=API_HASH, workdir=SESSIONS_DIR)
    try:
        await temp_client.connect()
        me = await temp_client.get_me()

        status = await temp_client.invoke(GetStarsStatus(peer=InputPeerSelf()))
        stars = status.balance.amount

        text = (
            f"👤 **Account:** {me.first_name}\n"
            f"🆔 **ID:** `{me.id}`\n"
            f"⭐ **Stars Balance:** `{stars}`"
        )

        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚪 Logout", callback_data=f"logout|{s_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data="my_sessions")],
            ]
        )
        await message.edit_text(text, reply_markup=kb)

    except Exception as e:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="my_sessions")]])
        await message.edit_text(f"❌ সেশন ইরর: {e}", reply_markup=kb)

    finally:
        try:
            await temp_client.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    bot.run()