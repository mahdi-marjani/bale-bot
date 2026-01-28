import telebot
import telebot.apihelper as apihelper
import os
from dotenv import load_dotenv
import json
import asyncio
import threading
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.utils import get_peer_id

# ================== ENV ==================
load_dotenv()
apihelper.API_URL = "https://tapi.bale.ai/bot{0}/{1}"
BALE_TOKEN     = os.getenv("BALE_BOT_TOKEN")
TG_API_ID      = int(os.getenv("TG_API_ID"))
TG_API_HASH    = os.getenv("TG_API_HASH")
TG_SESSION     = os.getenv("TG_SESSION_NAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# ================== BOT ==================
bot = telebot.TeleBot(BALE_TOKEN)

# ================== TELETHON ==================
os.makedirs("tg-sessions", exist_ok=True)
client = TelegramClient(
    os.path.join("tg-sessions", TG_SESSION),
    TG_API_ID,
    TG_API_HASH
)
tg_loop = asyncio.new_event_loop()

# ================== FILES ==================
CHANNELS_FILE = "channels.json"
ADMINS_FILE = "admins.json"
os.makedirs("downloads", exist_ok=True)

# ================== STORAGE ==================
def load_monitored():
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    return {}

def save_monitored():
    with open(CHANNELS_FILE, "w") as f:
        json.dump({str(k): v for k, v in monitored.items()}, f)

monitored = load_monitored()

def load_admins():
    if os.path.exists(ADMINS_FILE):
        with open(ADMINS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_admins():
    with open(ADMINS_FILE, "w") as f:
        json.dump(list(admins), f)

admins = load_admins()

# ================== AUTH CHECK ==================
def is_admin(message):
    if message.chat.type != "private":
        return False
    user_id = message.from_user.id
    if user_id not in admins:
        bot.reply_to(message, "Access denied. You are not an admin.")
        return False
    return True

# ================== BALE COMMANDS ==================
@bot.message_handler(commands=["start"])
def start_handler(message):
    if not is_admin(message):
        return
    bot.reply_to(
        message,
        "Commands:\n"
        "/add @tg_channel @bale_channel - add Telegram channel and target Bale channel\n"
        "/list - show monitored channels\n"
        "/remove @tg_channel - remove channel\n"
        "/login password - become admin\n"
        "/admins - list admins\n"
        "/remove_admin user_id - remove admin"
    )

@bot.message_handler(commands=["login"])
def login_handler(message):
    if message.chat.type != "private":
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /login password")
        return
    password = args[1].strip()
    if password != ADMIN_PASSWORD:
        bot.reply_to(message, "Incorrect password.")
        return
    user_id = message.from_user.id
    if user_id in admins:
        bot.reply_to(message, "You are already an admin.")
        return
    admins.add(user_id)
    save_admins()
    bot.reply_to(message, "You are now an admin.")

@bot.message_handler(commands=["admins"])
def admins_handler(message):
    if not is_admin(message):
        return
    if not admins:
        bot.reply_to(message, "No admins.")
        return
    text = "Admins:\n"
    for admin_id in admins:
        text += f"- ID: {admin_id}\n"
    bot.reply_to(message, text)

@bot.message_handler(commands=["remove_admin"])
def remove_admin_handler(message):
    if not is_admin(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /remove_admin user_id")
        return
    try:
        admin_id = int(args[1].strip())
    except ValueError:
        bot.reply_to(message, "Invalid user_id.")
        return
    if admin_id not in admins:
        bot.reply_to(message, "Not an admin.")
        return
    admins.remove(admin_id)
    save_admins()
    bot.reply_to(message, f"Removed admin: {admin_id}")

@bot.message_handler(commands=["list"])
def list_handler(message):
    if not is_admin(message):
        return
    if not monitored:
        bot.reply_to(message, "No channels monitored.")
        return
    text = "Monitored channels:\n"
    for chat_id, info in monitored.items():
        name = info["name"]
        bale_channel = info["bale_channel"]
        text += f"- {name} (TG ID: {chat_id}) -> Bale: {bale_channel}\n"
    bot.reply_to(message, text)

@bot.message_handler(commands=["add"])
def add_handler(message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Usage: /add @tg_channel @bale_channel")
        return
    tg_channel = args[1].strip()
    bale_channel = args[2].strip()
    asyncio.run_coroutine_threadsafe(
        add_channel(tg_channel, bale_channel, message),
        tg_loop
    )

@bot.message_handler(commands=["remove"])
def remove_handler(message):
    if not is_admin(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /remove @tg_channel")
        return
    tg_channel = args[1].strip()
    asyncio.run_coroutine_threadsafe(
        remove_channel(tg_channel, message),
        tg_loop
    )

# ================== ASYNC OPS ==================
async def add_channel(tg_channel_input, bale_channel, message):
    try:
        entity = await client.get_entity(tg_channel_input)
        peer_id = get_peer_id(entity)
        if peer_id in monitored:
            bot.reply_to(message, "Already monitoring.")
            return
        await client(JoinChannelRequest(entity))
        name = entity.username or entity.title or "Unknown"
        monitored[peer_id] = {"name": name, "bale_channel": bale_channel}
        save_monitored()
        bot.reply_to(message, f"Added: {name} -> {bale_channel}")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

async def remove_channel(tg_channel_input, message):
    try:
        entity = await client.get_entity(tg_channel_input)
        peer_id = get_peer_id(entity)
        if peer_id not in monitored:
            bot.reply_to(message, "Not monitored.")
            return
        await client(LeaveChannelRequest(entity))
        info = monitored.pop(peer_id)
        save_monitored()
        bot.reply_to(message, f"Removed: {info['name']}")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

# ================== TELEGRAM LISTENER ==================
@client.on(events.NewMessage)
async def new_message_handler(event):
    chat_id = event.chat_id
    if chat_id not in monitored:
        return
    bale_channel = monitored[chat_id]["bale_channel"]
    try:
        msg = event.message
        caption = msg.message or ""
        if hasattr(event.chat, "username") and event.chat.username:
            source = f"https://t.me/{event.chat.username}/{msg.id}"
        else:
            source = f"https://t.me/c/{str(chat_id)[4:]}/{msg.id}"
        caption = f"{caption}\n\nSource: {source}" if caption else f"Source: {source}"
        if msg.media:
            file_path = await event.download_media(file="./downloads/")
            if file_path:
                with open(file_path, "rb") as f:
                    if msg.photo:
                        bot.send_photo(bale_channel, f, caption=caption)
                    elif msg.video:
                        bot.send_video(bale_channel, f, caption=caption)
                    elif msg.audio:
                        bot.send_audio(bale_channel, f, caption=caption)
                    elif msg.document:
                        bot.send_document(bale_channel, f, caption=caption)
                os.remove(file_path)
        else:
            bot.send_message(bale_channel, caption)
    except Exception as e:
        print("Forward error:", e)

# ================== START TELETHON ==================
def start_telegram_client():
    asyncio.set_event_loop(tg_loop)
    async def runner():
        await client.start()
        print("Telegram client running")
        await client.run_until_disconnected()
    tg_loop.run_until_complete(runner())

threading.Thread(
    target=start_telegram_client,
    daemon=True
).start()

# ================== START BALE BOT ==================
bot.infinity_polling(skip_pending=True)