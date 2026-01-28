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

BALE_TOKEN   = os.getenv("BALE_BOT_TOKEN")
BALE_CHANNEL = os.getenv("BALE_CHANNEL")

TG_API_ID   = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION  = os.getenv("TG_SESSION_NAME")

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

# ================== BALE COMMANDS ==================
@bot.message_handler(commands=["start"])
def start_handler(message):
    if message.chat.type != "private":
        return
    bot.reply_to(
        message,
        "Commands:\n"
        "/add @channel - add Telegram channel\n"
        "/list - show monitored channels\n"
        "/remove @channel - remove channel"
    )

@bot.message_handler(commands=["list"])
def list_handler(message):
    if message.chat.type != "private":
        return

    if not monitored:
        bot.reply_to(message, "No channels monitored.")
        return

    text = "Monitored channels:\n"
    for chat_id, name in monitored.items():
        text += f"- {name} (ID: {chat_id})\n"

    bot.reply_to(message, text)

@bot.message_handler(commands=["add"])
def add_handler(message):
    if message.chat.type != "private":
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /add @username")
        return

    channel = args[1].strip()
    asyncio.run_coroutine_threadsafe(
        add_channel(channel, message),
        tg_loop
    )

@bot.message_handler(commands=["remove"])
def remove_handler(message):
    if message.chat.type != "private":
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /remove @username")
        return

    channel = args[1].strip()
    asyncio.run_coroutine_threadsafe(
        remove_channel(channel, message),
        tg_loop
    )

# ================== ASYNC OPS ==================
async def add_channel(channel_input, message):
    try:
        entity = await client.get_entity(channel_input)
        peer_id = get_peer_id(entity)

        if peer_id in monitored:
            bot.reply_to(message, "Already monitoring.")
            return

        await client(JoinChannelRequest(entity))

        name = entity.username or entity.title or "Unknown"
        monitored[peer_id] = name
        save_monitored()

        bot.reply_to(message, f"Added: {name}")

    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

async def remove_channel(channel_input, message):
    try:
        entity = await client.get_entity(channel_input)
        peer_id = get_peer_id(entity)

        if peer_id not in monitored:
            bot.reply_to(message, "Not monitored.")
            return

        await client(LeaveChannelRequest(entity))

        name = monitored.pop(peer_id)
        save_monitored()

        bot.reply_to(message, f"Removed: {name}")

    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

# ================== TELEGRAM LISTENER ==================
@client.on(events.NewMessage)
async def new_message_handler(event):
    chat_id = event.chat_id
    if chat_id not in monitored:
        return

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
                        bot.send_photo(BALE_CHANNEL, f, caption=caption)
                    elif msg.video:
                        bot.send_video(BALE_CHANNEL, f, caption=caption)
                    elif msg.audio:
                        bot.send_audio(BALE_CHANNEL, f, caption=caption)
                    elif msg.document:
                        bot.send_document(BALE_CHANNEL, f, caption=caption)
                os.remove(file_path)
        else:
            bot.send_message(BALE_CHANNEL, caption)

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
