import os
import json
import asyncio
import threading
import re
from datetime import datetime
from dotenv import load_dotenv
import telebot
import telebot.apihelper as apihelper
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest, ImportChatInviteRequest
from telethon.tl.types import (
    MessageEntityTextUrl, MessageEntityUrl, MessageEntityMention,
    MessageEntityHashtag, KeyboardButtonCallback, KeyboardButtonUrl
)
from telethon.utils import get_display_name

load_dotenv()

BALE_TOKEN = os.getenv("BALE_BOT_TOKEN")
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION = os.getenv("TG_SESSION_NAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

apihelper.API_URL = "https://tapi.bale.ai/bot{0}/{1}"
bot = telebot.TeleBot(BALE_TOKEN)

client = TelegramClient(
    os.path.join("tg-sessions", TG_SESSION),
    TG_API_ID,
    TG_API_HASH
)

CHUNK_SIZE = 10 * 1024 * 1024
ADMINS_FILE = "admins.json"
BUTTON_LISTS = {}

def load_admins():
    if os.path.exists(ADMINS_FILE):
        with open(ADMINS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_admins():
    with open(ADMINS_FILE, "w") as f:
        json.dump(list(admins), f)

admins = load_admins()

os.makedirs("downloads", exist_ok=True)
os.makedirs("splits", exist_ok=True)

PENDING_UPLOADS = {}
BALE_TO_TG = {}  # bale_msg_id -> (tg_chat_id, tg_msg_id)

def split_file(file_path, chunk_size):
    parts = []
    base = os.path.splitext(file_path)[0]
    ext = os.path.splitext(file_path)[1]
    with open(file_path, 'rb') as f:
        part_num = 1
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            part_path = f"{base}.part{part_num}{ext}"
            with open(part_path, 'wb') as pf:
                pf.write(chunk)
            parts.append(part_path)
            part_num += 1
    return parts

def entities_to_html(text, entities):
    if not entities:
        return text
    html = []
    last = 0
    for entity in sorted(entities, key=lambda e: e.offset):
        start = entity.offset
        end = entity.offset + entity.length
        if start > last:
            html.append(text[last:start])
        if isinstance(entity, MessageEntityTextUrl):
            url = entity.url
            link_text = text[start:end]
            html.append(f'<a href="{url}">{link_text}</a>')
        elif isinstance(entity, MessageEntityUrl):
            url = text[start:end]
            html.append(f'<a href="{url}">{url}</a>')
        elif isinstance(entity, MessageEntityMention):
            mention = text[start:end]
            html.append(f'<a href="https://t.me/{mention[1:]}">{mention}</a>')
        elif isinstance(entity, MessageEntityHashtag):
            html.append(f'<a href="https://t.me/search?q={text[start:end]}">{text[start:end]}</a>')
        else:
            html.append(text[start:end])
        last = end
    if last < len(text):
        html.append(text[last:])
    return ''.join(html)

def format_buttons_list(buttons, start_index=1):
    lines = []
    for i, btn in enumerate(buttons, start=start_index):
        if btn['type'] == 'callback':
            lines.append(f"{i}: {btn['text']} (callback)")
        else:
            lines.append(f"{i}: {btn['text']} (url: {btn['url']})")
    return "\n".join(lines)

def is_admin(user_id):
    return user_id in admins

@bot.message_handler(commands=['start', 'help'])
def start_handler(message):
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    if not is_admin(user_id):
        bot.reply_to(message, "Use /login <password> to authenticate.")
        return
    help_text = (
        "📋 *Commands:*\n"
        "/start - Show this help\n"
        "/login <password> - Authenticate\n"
        "/join <@channel|invite_link> - Join a Telegram channel/group\n"
        "/leave <@channel> - Leave a Telegram channel/group\n"
        "/msg <@user> text - Send text message\n"
        "/sendfile <@user> - Send a file (then upload the file)\n"
        "/history <@chat> [count] - Get recent messages (text only)\n"
        "/download [url] - Download file from a message (reply to a history message or provide URL)\n"
        "/admins - List admins\n"
        "/remove_admin <user_id> - Remove admin\n"
        "/clickbutton <index> - Reply to a button list message to click a button"
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['login'])
def login_handler(message):
    if message.chat.type != "private":
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or args[1] != ADMIN_PASSWORD:
        bot.reply_to(message, "Invalid password.")
        return
    user_id = message.from_user.id
    if user_id in admins:
        bot.reply_to(message, "You are already an admin.")
        return
    admins.add(user_id)
    save_admins()
    bot.reply_to(message, "✅ You are now admin.")

@bot.message_handler(commands=['admins'])
def admins_handler(message):
    if not is_admin(message.from_user.id):
        return
    if not admins:
        bot.reply_to(message, "No admins.")
        return
    text = "Admins:\n"
    for admin_id in admins:
        text += f"- ID: {admin_id}\n"
    bot.reply_to(message, text)

@bot.message_handler(commands=['remove_admin'])
def remove_admin_handler(message):
    if not is_admin(message.from_user.id):
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

@bot.message_handler(commands=['join'])
def join_handler(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /join @channel_or_group or /join invite_link")
        return
    target = args[1].strip()
    asyncio.run_coroutine_threadsafe(join_channel(target, message), tg_loop)

@bot.message_handler(commands=['leave'])
def leave_handler(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /leave @channel_or_group")
        return
    target = args[1].strip()
    asyncio.run_coroutine_threadsafe(leave_channel(target, message), tg_loop)

@bot.message_handler(commands=['msg'])
def msg_handler(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        bot.reply_to(message, "Usage: /msg @username_or_id text")
        return
    target = args[1]
    text = args[2]
    asyncio.run_coroutine_threadsafe(send_tg_text(target, text, message), tg_loop)

@bot.message_handler(commands=['sendfile'])
def sendfile_handler(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /sendfile @username_or_id\nThen send the file.")
        return
    target = args[1]
    PENDING_UPLOADS[message.from_user.id] = target
    bot.reply_to(message, f"📤 Send the file now (will be sent to {target}).")

@bot.message_handler(commands=['history'])
def history_handler(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /history @chat [count] (default 10)")
        return
    target = args[1]
    count = 10
    if len(args) >= 3:
        try:
            count = int(args[2])
            if count > 50:
                count = 50
                bot.reply_to(message, "Limit reduced to 50 messages.")
        except ValueError:
            bot.reply_to(message, "Invalid count. Using 10.")
            count = 10
    asyncio.run_coroutine_threadsafe(get_history(target, count, message), tg_loop)

@bot.message_handler(commands=['download'])
def download_handler(message):
    if not is_admin(message.from_user.id):
        return
    # Send immediate acknowledgment
    progress_msg = bot.reply_to(message, "⏳ Processing download request...")
    # Check if it's a reply to a message with mapping
    if message.reply_to_message:
        orig_bale_id = message.reply_to_message.message_id
        if orig_bale_id in BALE_TO_TG:
            chat_id, tg_msg_id = BALE_TO_TG[orig_bale_id]
            asyncio.run_coroutine_threadsafe(
                download_message(chat_id, tg_msg_id, message, progress_msg),
                tg_loop
            )
            return
    # If not a reply, try to parse URL from args
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.edit_message_text("❌ Usage: /download <url> or reply to a message from /history", 
                              message.chat.id, progress_msg.message_id)
        return
    url = args[1].strip()
    # Parse Telegram message URL
    match = re.search(r't\.me/(?:c/)?([^/]+)/(\d+)', url)
    if not match:
        bot.edit_message_text("❌ Invalid Telegram message URL.", message.chat.id, progress_msg.message_id)
        return
    chat_part = match.group(1)
    msg_id = int(match.group(2))
    asyncio.run_coroutine_threadsafe(
        download_message_by_url(chat_part, msg_id, message, progress_msg),
        tg_loop
    )

@bot.message_handler(commands=['clickbutton'])
def clickbutton_handler(message):
    if not is_admin(message.from_user.id):
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Please reply to a button list message with this command.")
        return
    list_msg_id = message.reply_to_message.message_id
    if list_msg_id not in BUTTON_LISTS:
        bot.reply_to(message, "This message does not contain a button list.")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: reply to a button list with /clickbutton <index>")
        return
    try:
        idx = int(args[1])
    except ValueError:
        bot.reply_to(message, "Invalid index. Use a number.")
        return
    tg_chat_id, tg_msg_id, buttons = BUTTON_LISTS[list_msg_id]
    if idx < 1 or idx > len(buttons):
        bot.reply_to(message, f"Index out of range. Valid: 1..{len(buttons)}")
        return
    btn = buttons[idx-1]
    if btn['type'] == 'callback':
        asyncio.run_coroutine_threadsafe(
            send_callback_answer(tg_chat_id, tg_msg_id, btn['data'], message),
            tg_loop
        )
    else:
        url = btn['url']
        if url.startswith("https://t.me/") or url.startswith("http://t.me/"):
            bot.reply_to(message, f"URL button: {btn['text']}\nLink: {url}\nUse /join {url} to join if it's a Telegram group/channel.")
        else:
            bot.reply_to(message, f"URL button: {btn['text']}\nLink: {url}")

@bot.message_handler(content_types=['document', 'photo', 'video', 'audio'])
def file_received(message):
    if not is_admin(message.from_user.id):
        return
    if message.from_user.id not in PENDING_UPLOADS:
        bot.reply_to(message, "No pending upload. Use /sendfile first.")
        return
    target = PENDING_UPLOADS.pop(message.from_user.id)
    file_info = None
    if message.document:
        file_info = message.document
    elif message.photo:
        file_info = message.photo[-1]
    elif message.video:
        file_info = message.video
    elif message.audio:
        file_info = message.audio
    if not file_info:
        bot.reply_to(message, "Unsupported file type.")
        return
    file_id = file_info.file_id
    file_path = bot.get_file(file_id).file_path
    downloaded = bot.download_file(file_path)
    temp_path = f"downloads/tg_{file_id}.tmp"
    with open(temp_path, 'wb') as f:
        f.write(downloaded)
    asyncio.run_coroutine_threadsafe(send_tg_file(target, temp_path, message), tg_loop)

async def join_channel(target, reply_to_bale_msg=None):
    try:
        if target.startswith('http://') or target.startswith('https://') or target.startswith('t.me/'):
            match = re.search(r'/(?:joinchat/|\+)([a-zA-Z0-9_-]+)', target)
            if not match:
                raise ValueError(f"Could not extract invite hash from {target}")
            hash_part = match.group(1)
            await client(ImportChatInviteRequest(hash_part))
            if reply_to_bale_msg:
                bot.reply_to(reply_to_bale_msg, f"✅ Joined {target}")
        else:
            entity = await client.get_entity(target)
            await client(JoinChannelRequest(entity))
            if reply_to_bale_msg:
                bot.reply_to(reply_to_bale_msg, f"✅ Joined {target}")
    except Exception as e:
        if reply_to_bale_msg:
            bot.reply_to(reply_to_bale_msg, f"❌ Error: {e}")

async def leave_channel(target, reply_to_bale_msg=None):
    try:
        entity = await client.get_entity(target)
        await client(LeaveChannelRequest(entity))
        if reply_to_bale_msg:
            bot.reply_to(reply_to_bale_msg, f"✅ Left {target}")
    except Exception as e:
        if reply_to_bale_msg:
            bot.reply_to(reply_to_bale_msg, f"❌ Error: {e}")

async def send_tg_text(target, text, reply_to_bale_msg=None):
    try:
        entity = await client.get_entity(target)
        await client.send_message(entity, text)
        if reply_to_bale_msg:
            bot.reply_to(reply_to_bale_msg, "✅ Message sent.")
    except Exception as e:
        if reply_to_bale_msg:
            bot.reply_to(reply_to_bale_msg, f"❌ Error: {e}")

async def send_tg_file(target, file_path, reply_to_bale_msg=None):
    try:
        entity = await client.get_entity(target)
        size = os.path.getsize(file_path)
        if size > CHUNK_SIZE:
            parts = split_file(file_path, CHUNK_SIZE)
            for part in parts:
                await client.send_file(entity, part)
                os.remove(part)
            bot.reply_to(reply_to_bale_msg, "✅ File sent in parts.")
        else:
            await client.send_file(entity, file_path)
            bot.reply_to(reply_to_bale_msg, "✅ File sent.")
        os.remove(file_path)
    except Exception as e:
        bot.reply_to(reply_to_bale_msg, f"❌ Error: {e}")

async def get_history(chat_identifier, limit, reply_to_bale_msg):
    try:
        entity = await client.get_entity(chat_identifier)
        messages = await client.get_messages(entity, limit=limit)
        if not messages:
            bot.reply_to(reply_to_bale_msg, "No messages found.")
            return

        for msg in reversed(messages):  # oldest first
            # Sender name
            sender = await msg.get_sender()
            sender_name = get_display_name(sender) if sender else "Unknown"
            # Date
            date = msg.date.strftime("%Y-%m-%d %H:%M:%S")
            # Message link
            link = None
            if hasattr(entity, 'username') and entity.username:
                link = f"https://t.me/{entity.username}/{msg.id}"
            elif hasattr(entity, 'id') and isinstance(entity.id, int) and entity.id < 0:
                channel_id = str(entity.id)[4:]
                link = f"https://t.me/c/{channel_id}/{msg.id}"
            # Text
            text = msg.message or ""
            if msg.entities:
                text = entities_to_html(text, msg.entities)
            # Media info (text only)
            media_info = ""
            if msg.media:
                media_type = "📎 File"
                size = None
                if msg.document:
                    media_type = "📄 Document"
                    if msg.document.attributes:
                        for attr in msg.document.attributes:
                            if hasattr(attr, 'file_name'):
                                media_type += f" {attr.file_name}"
                    size = msg.document.size
                elif msg.photo:
                    media_type = "🖼 Photo"
                    if msg.photo.sizes:
                        largest = msg.photo.sizes[-1]
                        size = largest.size if hasattr(largest, 'size') else None
                elif msg.video:
                    media_type = "🎥 Video"
                    size = msg.video.size
                elif msg.audio:
                    media_type = "🎵 Audio"
                    size = msg.audio.size
                elif msg.voice:
                    media_type = "🎙 Voice"
                    size = msg.voice.size
                if size:
                    size_mb = size / (1024 * 1024)
                    media_info = f"{media_type} ({size_mb:.1f} MB)"
                else:
                    media_info = media_type

            # Build message content
            content = f"👤 {sender_name} • {date}\n"
            if text:
                content += text + "\n"
            if media_info:
                content += media_info + "\n"
            if link:
                content += f"🔗 <a href='{link}'>Link</a>"

            # Send the message and store mapping for replies
            bale_msg = bot.send_message(reply_to_bale_msg.chat.id, content, parse_mode="HTML")
            BALE_TO_TG[bale_msg.message_id] = (entity.id, msg.id)

            # Handle buttons (if any)
            buttons = msg.buttons if hasattr(msg, 'buttons') and msg.buttons else None
            if buttons:
                button_list = []
                for row in buttons:
                    for btn in row:
                        if isinstance(btn.button, KeyboardButtonCallback):
                            data = btn.button.data
                            if isinstance(data, bytes):
                                data = data.decode('utf-8')
                            button_list.append({
                                'type': 'callback',
                                'text': btn.text,
                                'data': data
                            })
                        elif isinstance(btn.button, KeyboardButtonUrl):
                            button_list.append({
                                'type': 'url',
                                'text': btn.text,
                                'url': btn.button.url
                            })
                if button_list:
                    list_text = "📋 *Buttons:*\n" + format_buttons_list(button_list)
                    list_msg = bot.send_message(reply_to_bale_msg.chat.id, list_text, parse_mode="Markdown")
                    BUTTON_LISTS[list_msg.message_id] = (entity.id, msg.id, button_list)
    except Exception as e:
        bot.reply_to(reply_to_bale_msg, f"❌ Error fetching history: {e}")

async def download_message(chat_id, msg_id, bale_message, progress_msg):
    try:
        entity = await client.get_entity(chat_id)
        msg = await client.get_messages(entity, ids=msg_id)
        if not msg:
            bot.edit_message_text("❌ Message not found.", bale_message.chat.id, progress_msg.message_id)
            return
        if not msg.media:
            bot.edit_message_text("❌ No media in this message.", bale_message.chat.id, progress_msg.message_id)
            return

        # Notify download start
        file_size_mb = None
        if msg.document and msg.document.size:
            file_size_mb = msg.document.size / (1024 * 1024)
            bot.edit_message_text(f"⬇️ Downloading file... ({file_size_mb:.1f} MB)", 
                                  bale_message.chat.id, progress_msg.message_id)
        else:
            bot.edit_message_text("⬇️ Downloading file...", 
                                  bale_message.chat.id, progress_msg.message_id)

        # Download the file
        file_path = await client.download_media(msg, file="downloads/")
        if not file_path:
            bot.edit_message_text("❌ Failed to download file.", bale_message.chat.id, progress_msg.message_id)
            return

        # Check size and send
        size = os.path.getsize(file_path)
        bot.edit_message_text(f"📤 Sending file ({size/(1024*1024):.1f} MB)...", 
                              bale_message.chat.id, progress_msg.message_id)

        if size > CHUNK_SIZE:
            parts = split_file(file_path, CHUNK_SIZE)
            # Get base name without extension for the reassembly command
            base_name = os.path.basename(file_path)
            base_no_ext, ext = os.path.splitext(base_name)
            # Send parts
            for part in parts:
                with open(part, 'rb') as f:
                    bot.send_document(bale_message.chat.id, f, caption=f"📎 {os.path.basename(part)}")
                os.remove(part)
            # Correct reassembly command: cat base_no_ext.part*.ext > base_name
            # Example: cat Three-Days-Grace-Animal-I-Have-Become.part*.mp4 > Three-Days-Grace-Animal-I-Have-Become.mp4
            reassembly = f"`cat {base_no_ext}.part*{ext} > {base_name}`"
            bot.send_message(bale_message.chat.id, 
                             f"📦 File split into {len(parts)} parts. To reassemble:\n{reassembly}", 
                             parse_mode="Markdown")
        else:
            with open(file_path, 'rb') as f:
                bot.send_document(bale_message.chat.id, f, caption=f"📎 {os.path.basename(file_path)}")
        os.remove(file_path)
        # Delete progress message
        bot.delete_message(bale_message.chat.id, progress_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error downloading: {e}", bale_message.chat.id, progress_msg.message_id)

async def download_message_by_url(chat_part, msg_id, bale_message, progress_msg):
    try:
        if chat_part.isdigit():
            # Might be a channel ID (e.g., 123456789)
            try:
                entity = await client.get_entity(int(chat_part))
            except:
                # Try with -100 prefix if it's a channel
                try:
                    entity = await client.get_entity(int(f"-100{chat_part}"))
                except:
                    raise Exception("Cannot find entity with that ID.")
        else:
            # Username
            entity = await client.get_entity(f"@{chat_part}")
        await download_message(entity.id, msg_id, bale_message, progress_msg)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", bale_message.chat.id, progress_msg.message_id)

async def send_callback_answer(chat_id, message_id, callback_data, bale_message):
    try:
        entity = await client.get_entity(chat_id)
        result = await client(GetBotCallbackAnswerRequest(
            peer=entity,
            msg_id=message_id,
            data=callback_data.encode('utf-8')
        ))
        if result.message:
            bot.send_message(bale_message.chat.id, f"📝 {result.message}")
        elif result.alert:
            bot.send_message(bale_message.chat.id, f"🔔 {result.alert}")
        else:
            bot.send_message(bale_message.chat.id, "✅ Callback sent.")
    except Exception as e:
        bot.send_message(bale_message.chat.id, f"❌ Error sending callback: {e}")

@bot.message_handler(func=lambda m: m.reply_to_message and is_admin(m.from_user.id))
def reply_handler(message):
    if not message.reply_to_message:
        return
    orig_bale_id = message.reply_to_message.message_id
    if orig_bale_id in BALE_TO_TG:
        chat_id, tg_msg_id = BALE_TO_TG[orig_bale_id]
        text = message.text
        if text.startswith('/'):
            return
        asyncio.run_coroutine_threadsafe(
            client.send_message(chat_id, text, reply_to=tg_msg_id),
            tg_loop
        )
        bot.reply_to(message, "✅ Reply sent.")

def start_telegram():
    global tg_loop
    tg_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(tg_loop)
    async def runner():
        await client.start()
        print("Telegram client ready")
        await client.run_until_disconnected()
    tg_loop.run_until_complete(runner())

threading.Thread(target=start_telegram, daemon=True).start()

bot.infinity_polling(skip_pending=True)
