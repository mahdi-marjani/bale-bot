import os
import json
import asyncio
import threading
import re
from dotenv import load_dotenv
import telebot
import telebot.apihelper as apihelper
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest, ImportChatInviteRequest, ForwardMessagesRequest
from telethon.tl.types import KeyboardButtonCallback, KeyboardButtonUrl, MessageEntityTextUrl, MessageEntityUrl, MessageEntityMention, MessageEntityHashtag, ReplyKeyboardMarkup
from telethon.utils import get_display_name

load_dotenv()

BALE_TOKEN = os.getenv("BALE_BOT_TOKEN")
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION = os.getenv("TG_SESSION_NAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

apihelper.API_URL = "https://tapi.bale.ai/bot{0}/{1}"
apihelper.FILE_URL = "https://tapi.bale.ai/file/bot{0}/{1}"
bot = telebot.TeleBot(BALE_TOKEN)

client = TelegramClient(
    os.path.join("tg-sessions", TG_SESSION),
    TG_API_ID,
    TG_API_HASH
)

CHUNK_SIZE = 10 * 1024 * 1024
ADMINS_FILE = "admins.json"
BUTTON_LISTS = {}
REPLY_KEYBOARD_LISTS = {}

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
BALE_TO_TG = {}

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

def format_buttons_list(buttons, start_index=1):
    lines = []
    for i, btn in enumerate(buttons, start=start_index):
        if btn['type'] == 'callback':
            lines.append(f"{i}: {btn['text']} (callback)")
        else:
            lines.append(f"{i}: {btn['text']} (url: {btn['url']})")
    return "\n".join(lines)

def format_reply_keyboard_list(buttons, start_index=1):
    lines = []
    for i, btn_text in enumerate(buttons, start=start_index):
        lines.append(f'{i}: "{btn_text}"')
    return "\n".join(lines)

def is_admin(user_id):
    return user_id in admins

def format_with_links(text, entities):
    if not entities:
        return text
    parts = []
    last = 0
    for entity in sorted(entities, key=lambda e: e.offset):
        start = entity.offset
        end = entity.offset + entity.length
        if start > last:
            parts.append(text[last:start])
        linked_text = text[start:end]
        url = None
        if isinstance(entity, MessageEntityTextUrl):
            url = entity.url
        elif isinstance(entity, MessageEntityUrl):
            url = linked_text
        elif isinstance(entity, MessageEntityMention):
            username = linked_text[1:] if linked_text.startswith('@') else linked_text
            url = f"https://t.me/{username}"
        elif isinstance(entity, MessageEntityHashtag):
            url = f"https://t.me/search?q={linked_text}"
        if url:
            parts.append(f"{linked_text} ({url})")
        else:
            parts.append(linked_text)
        last = end
    if last < len(text):
        parts.append(text[last:])
    return ''.join(parts)

@bot.message_handler(commands=['start', 'help'])
def start_handler(message):
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    if not is_admin(user_id):
        bot.reply_to(message, "Use /login <password> to authenticate.")
        return
    help_text = (
        "/start - Show this help\n"
        "/login <password> - Authenticate\n"
        "/join <@channel|invite_link> - Join a Telegram channel/group\n"
        "/leave <@channel> - Leave a Telegram channel/group\n"
        "/msg <@user> text - Send text message\n"
        "/sendfile <@user> - Send a file (then upload the file)\n"
        "/history <@chat> [count] - Get recent messages\n"
        "/download [url] - Download file from a message\n"
        "/user <@user|id> - Get user info\n"
        "/admins - List admins\n"
        "/remove_admin <user_id> - Remove admin\n"
        "/clickbutton <index> - Reply to a button list message to click an inline button\n"
        "/forward <@target> - Forward replied message to target chat\n"
        "/botmenu <@bot> - Get bot's menu (text + inline buttons + reply keyboard as clickable numbers)\n"
        "/clickkeyboard <index> - Reply to a reply keyboard list message to press a keyboard button (alternative to clickable buttons)"
    )
    bot.reply_to(message, help_text)

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
    bot.reply_to(message, "You are now admin.")

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
    asyncio.run_coroutine_threadsafe(send_tg_text(target, text), tg_loop)
    bot.send_message(
        message.chat.id,
        f"Message sent to {target}.",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("History (5)", callback_data=f"history:{target}:5")
        )
    )

@bot.message_handler(commands=['sendfile'])
def sendfile_handler(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /sendfile @username_or_id\nThen send the file.")
        return
    target = args[1]
    PENDING_UPLOADS[message.from_user.id] = (target, message.chat.id, message.message_id)
    bot.reply_to(message, f"Send the file now (will be sent to {target}).\nSupported: photo, video, audio, document. Max size per part: {CHUNK_SIZE//(1024*1024)}MB")

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

@bot.message_handler(commands=['user'])
def user_handler(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /user @username or user_id")
        return
    target = args[1].strip()
    asyncio.run_coroutine_threadsafe(get_user_info(target, message), tg_loop)

@bot.message_handler(commands=['download'])
def download_handler(message):
    if not is_admin(message.from_user.id):
        return
    progress_msg = bot.reply_to(message, "Processing download request...")
    if message.reply_to_message:
        orig_bale_id = message.reply_to_message.message_id
        if orig_bale_id in BALE_TO_TG:
            chat_id, tg_msg_id = BALE_TO_TG[orig_bale_id]
            asyncio.run_coroutine_threadsafe(
                download_message(chat_id, tg_msg_id, message, progress_msg),
                tg_loop
            )
            return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.edit_message_text("Usage: /download <url> or reply to a message from /history",
                              message.chat.id, progress_msg.message_id)
        return
    url = args[1].strip()
    match = re.search(r't\.me/(?:c/)?([^/]+)/(\d+)', url)
    if not match:
        bot.edit_message_text("Invalid Telegram message URL.", message.chat.id, progress_msg.message_id)
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
        bot.reply_to(message, "This message does not contain an inline button list.")
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

@bot.message_handler(commands=['forward'])
def forward_handler(message):
    if not is_admin(message.from_user.id):
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Please reply to a message from /history to forward.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /forward @target (reply to a message from /history)")
        return
    target = args[1].strip()
    orig_bale_id = message.reply_to_message.message_id
    if orig_bale_id not in BALE_TO_TG:
        bot.reply_to(message, "Cannot forward this message. Only messages from /history can be forwarded.")
        return
    tg_chat_id, tg_msg_id = BALE_TO_TG[orig_bale_id]
    asyncio.run_coroutine_threadsafe(
        forward_tg_message(tg_chat_id, tg_msg_id, target, message),
        tg_loop
    )

@bot.message_handler(commands=['botmenu'])
def botmenu_handler(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /botmenu @bot_username")
        return
    bot_username = args[1].strip()
    if not bot_username.startswith('@'):
        bot_username = '@' + bot_username
    progress_msg = bot.reply_to(message, f"Fetching menu from {bot_username}...")
    asyncio.run_coroutine_threadsafe(
        get_bot_menu(bot_username, message, progress_msg),
        tg_loop
    )

@bot.message_handler(commands=['clickkeyboard'])
def clickkeyboard_handler(message):
    if not is_admin(message.from_user.id):
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Please reply to a reply keyboard list message with this command.")
        return
    list_msg_id = message.reply_to_message.message_id
    if list_msg_id not in REPLY_KEYBOARD_LISTS:
        bot.reply_to(message, "This message does not contain a reply keyboard list.")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: reply to a keyboard list with /clickkeyboard <index>")
        return
    try:
        idx = int(args[1])
    except ValueError:
        bot.reply_to(message, "Invalid index. Use a number.")
        return
    bot_entity, button_texts = REPLY_KEYBOARD_LISTS[list_msg_id]
    if idx < 1 or idx > len(button_texts):
        bot.reply_to(message, f"Index out of range. Valid: 1..{len(button_texts)}")
        return
    selected_text = button_texts[idx-1]
    asyncio.run_coroutine_threadsafe(
        send_tg_text(bot_entity.username if hasattr(bot_entity, 'username') else bot_entity.id, selected_text),
        tg_loop
    )
    bot.reply_to(message, f"Sent command '{selected_text}' to {bot_entity.username}.")

@bot.message_handler(content_types=['document', 'photo', 'video', 'audio'])
def file_received(message):
    if not is_admin(message.from_user.id):
        return
    if message.from_user.id not in PENDING_UPLOADS:
        bot.reply_to(message, "No pending upload. Use /sendfile first.")
        return

    target, admin_chat_id, admin_msg_id = PENDING_UPLOADS.pop(message.from_user.id)
    bot.send_message(admin_chat_id, f"File received. Sending to {target}...")

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
        bot.send_message(admin_chat_id, "Unsupported file type.")
        return

    file_id = file_info.file_id
    file_name = getattr(file_info, 'file_name', f"file_{file_id}")
    if message.photo:
        file_name = f"photo_{file_id}.jpg"

    status_msg = bot.send_message(admin_chat_id, f"Downloading {file_name}...")
    try:
        downloaded = bot.download_file(file_id)
        temp_path = f"downloads/bale_{file_id}_{os.path.basename(file_name)}"
        with open(temp_path, 'wb') as f:
            f.write(downloaded)
        bot.edit_message_text(f"Downloaded. Size: {os.path.getsize(temp_path)/(1024*1024):.1f} MB. Sending to Telegram...",
                              admin_chat_id, status_msg.message_id)
        asyncio.run_coroutine_threadsafe(
            send_tg_file(target, temp_path, message, admin_chat_id, status_msg.message_id),
            tg_loop
        )
    except Exception as e:
        bot.edit_message_text(f"Download failed: {str(e)}", admin_chat_id, status_msg.message_id)

async def join_channel(target, reply_to_bale_msg=None):
    try:
        if target.startswith('http://') or target.startswith('https://') or target.startswith('t.me/'):
            match = re.search(r'/(?:joinchat/|\+)([a-zA-Z0-9_-]+)', target)
            if not match:
                raise ValueError(f"Could not extract invite hash from {target}")
            hash_part = match.group(1)
            await client(ImportChatInviteRequest(hash_part))
            if reply_to_bale_msg:
                bot.reply_to(reply_to_bale_msg, f"Joined {target}")
        else:
            entity = await client.get_entity(target)
            await client(JoinChannelRequest(entity))
            if reply_to_bale_msg:
                bot.reply_to(reply_to_bale_msg, f"Joined {target}")
    except Exception as e:
        if reply_to_bale_msg:
            bot.reply_to(reply_to_bale_msg, f"Error: {e}")

async def leave_channel(target, reply_to_bale_msg=None):
    try:
        entity = await client.get_entity(target)
        await client(LeaveChannelRequest(entity))
        if reply_to_bale_msg:
            bot.reply_to(reply_to_bale_msg, f"Left {target}")
    except Exception as e:
        if reply_to_bale_msg:
            bot.reply_to(reply_to_bale_msg, f"Error: {e}")

async def send_tg_text(target, text):
    try:
        entity = await client.get_entity(target)
        await client.send_message(entity, text)
    except Exception as e:
        print(f"Error sending message: {e}")

async def send_tg_file(target, file_path, original_bale_message, admin_chat_id, status_msg_id):
    try:
        entity = await client.get_entity(target)
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)

        bot.edit_message_text(f"Sending {file_name} ({file_size/(1024*1024):.1f} MB) to {target}...",
                              admin_chat_id, status_msg_id)

        ext = os.path.splitext(file_name)[1].lower()
        is_audio = ext in ('.mp3', '.m4a', '.ogg', '.wav', '.flac')
        is_video = ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm')
        is_photo = ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')

        if file_size > CHUNK_SIZE:
            parts = split_file(file_path, CHUNK_SIZE)
            bot.edit_message_text(f"File large. Splitting into {len(parts)} parts...",
                                  admin_chat_id, status_msg_id)
            for idx, part_path in enumerate(parts, 1):
                await client.send_file(entity, part_path, force_document=True)
                os.remove(part_path)
                bot.send_message(admin_chat_id, f"Part {idx}/{len(parts)} sent.")
            reassembly_cmd = f"cat {os.path.splitext(file_name)[0]}.part*{ext} > {file_name}"
            bot.send_message(admin_chat_id,
                             f"Original file sent as {len(parts)} parts.\nTo reassemble on Linux:\n`{reassembly_cmd}`",
                             parse_mode="Markdown")
            os.remove(file_path)
            return

        if is_photo:
            await client.send_file(entity, file_path, force_document=False, photo=True)
        elif is_video:
            await client.send_file(entity, file_path, force_document=False, video=True)
        elif is_audio:
            await client.send_file(entity, file_path, force_document=False, audio=True)
        else:
            await client.send_file(entity, file_path, force_document=True)

        bot.edit_message_text(f"File {file_name} sent successfully to {target}.",
                              admin_chat_id, status_msg_id)
        os.remove(file_path)
        bot.send_message(admin_chat_id,
                         f"Sent to {target}.",
                         reply_markup=InlineKeyboardMarkup().add(
                             InlineKeyboardButton("History (5)", callback_data=f"history:{target}:5")
                         ))
    except Exception as e:
        bot.edit_message_text(f"Send failed: {str(e)}", admin_chat_id, status_msg_id)
        if os.path.exists(file_path):
            os.remove(file_path)

async def forward_tg_message(from_chat_id, msg_id, target, reply_to_bale_msg):
    try:
        to_entity = await client.get_entity(target)
        from_entity = await client.get_entity(from_chat_id)
        await client(ForwardMessagesRequest(
            from_peer=from_entity,
            id=[msg_id],
            to_peer=to_entity,
            drop_author=False,
            drop_media_captions=False,
            silent=False
        ))
        bot.send_message(
            reply_to_bale_msg.chat.id,
            f"Message forwarded to {target}.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("History (5)", callback_data=f"history:{target}:5")
            )
        )
    except Exception as e:
        bot.reply_to(reply_to_bale_msg, f"Error forwarding: {e}")

async def get_user_info(target, bale_message):
    try:
        entity = await client.get_entity(target)
        if hasattr(entity, 'username') and entity.username:
            username = f"@{entity.username}"
        else:
            username = "None"
        first_name = getattr(entity, 'first_name', '')
        last_name = getattr(entity, 'last_name', '')
        full_name = f"{first_name} {last_name}".strip() if first_name else (last_name or "None")
        bio = getattr(entity, 'about', 'None')
        status = getattr(entity, 'status', None)
        last_seen = "Unknown"
        if status:
            if hasattr(status, 'was_online'):
                last_seen = f"Last seen: {status.was_online.strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                last_seen = "Online"
        info = f"{full_name}\nID: {entity.id}\nUsername: {username}\n"
        if bio != 'None':
            info += f"Bio: {bio}\n"
        info += f"{last_seen}"
        photos = await client.get_profile_photos(entity, limit=1)
        if photos:
            photo = photos[0]
            file_path = await client.download_media(photo, file="downloads/")
            if file_path:
                with open(file_path, 'rb') as f:
                    bot.send_photo(bale_message.chat.id, f, caption=info)
                os.remove(file_path)
            else:
                bot.send_message(bale_message.chat.id, info)
        else:
            bot.send_message(bale_message.chat.id, info)
    except Exception as e:
        bot.reply_to(bale_message, f"Error: {e}")

async def get_bot_menu(bot_username, bale_message, progress_msg):
    try:
        entity = await client.get_entity(bot_username)
        bot.edit_message_text(f"Sending /start to {bot_username}...", bale_message.chat.id, progress_msg.message_id)
        await client.send_message(entity, "/start")
        await asyncio.sleep(2)
        messages = await client.get_messages(entity, limit=1)
        if not messages:
            bot.edit_message_text("No response from bot.", bale_message.chat.id, progress_msg.message_id)
            return
        response = messages[0]

        reply_keyboard = None
        if response.reply_markup and isinstance(response.reply_markup, ReplyKeyboardMarkup):
            reply_keyboard = response.reply_markup

        if response.text:
            sender = await response.get_sender()
            sender_name = get_display_name(sender) if sender else "Bot"
            date = response.date.strftime("%Y-%m-%d %H:%M:%S")
            link = None
            if hasattr(entity, 'username') and entity.username:
                link = f"https://t.me/{entity.username}/{response.id}"
            content = f"👤 {sender_name} • {date}\n{response.text}"
            if link:
                content += f"\n🔗 {link}"
            bale_msg = bot.send_message(bale_message.chat.id, content)
            BALE_TO_TG[bale_msg.message_id] = (entity.id, response.id)
        elif response.media:
            bot.edit_message_text("Bot replied with media. Use /history to see it.", bale_message.chat.id, progress_msg.message_id)
            return
        else:
            bot.edit_message_text("Empty response from bot.", bale_message.chat.id, progress_msg.message_id)
            return

        buttons = response.buttons if hasattr(response, 'buttons') and response.buttons else None
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
                inline_buttons = []
                for idx, b in enumerate(button_list, start=1):
                    if b['type'] == 'callback':
                        cb_data = f"cb:{entity.id}:{response.id}:{b['data']}"
                        inline_buttons.append(InlineKeyboardButton(str(idx), callback_data=cb_data))
                    else:
                        inline_buttons.append(InlineKeyboardButton(b['text'], url=b['url']))
                rows = [inline_buttons[i:i+3] for i in range(0, len(inline_buttons), 3)]
                markup = InlineKeyboardMarkup(rows)
                list_text = "Inline Buttons:\n" + format_buttons_list(button_list)
                bot.send_message(bale_message.chat.id, list_text, reply_markup=markup)
                BUTTON_LISTS[bale_msg.message_id] = (entity.id, response.id, button_list)

        if reply_keyboard:
            button_texts = []
            for row in reply_keyboard.rows:
                for btn in row.buttons:
                    button_texts.append(btn.text)
            if button_texts:
                reply_inline_buttons = []
                for idx, btn_text in enumerate(button_texts, start=1):
                    import uuid
                    action_id = str(uuid.uuid4())
                    REPLY_KB_ACTIONS[action_id] = (entity, btn_text)
                    reply_inline_buttons.append(InlineKeyboardButton(str(idx), callback_data=f"replykb:{action_id}"))
                rows = [reply_inline_buttons[i:i+5] for i in range(0, len(reply_inline_buttons), 5)]
                markup = InlineKeyboardMarkup(rows)
                list_text = "Reply Keyboard Buttons:\n" + format_reply_keyboard_list(button_texts)
                bot.send_message(bale_message.chat.id, list_text, reply_markup=markup)

        bot.delete_message(bale_message.chat.id, progress_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"Error: {e}", bale_message.chat.id, progress_msg.message_id)

REPLY_KB_ACTIONS = {}

async def send_reply_keyboard_press(bot_entity, button_text, bale_chat_id):
    try:
        await client.send_message(bot_entity, button_text)
        await asyncio.sleep(1)
        messages = await client.get_messages(bot_entity, limit=1)
        if messages:
            response = messages[0]
            if response.text:
                sender = await response.get_sender()
                sender_name = get_display_name(sender) if sender else bot_entity.username
                date = response.date.strftime("%Y-%m-%d %H:%M:%S")
                link = None
                if hasattr(bot_entity, 'username') and bot_entity.username:
                    link = f"https://t.me/{bot_entity.username}/{response.id}"
                content = f"👤 {sender_name} • {date}\n{response.text}"
                if link:
                    content += f"\n🔗 {link}"
                bot.send_message(bale_chat_id, content)
                bale_msg = bot.send_message(bale_chat_id, "Response shown above.")
                BALE_TO_TG[bale_msg.message_id] = (bot_entity.id, response.id)
            elif response.media:
                bot.send_message(bale_chat_id, "Bot replied with media. Use /history to see it.")
            else:
                bot.send_message(bale_chat_id, "No text response from bot.")
    except Exception as e:
        bot.send_message(bale_chat_id, f"Error sending keyboard press: {e}")

async def get_history(chat_identifier, limit, reply_to_bale_msg):
    try:
        entity = await client.get_entity(chat_identifier)
        messages = await client.get_messages(entity, limit=limit)
        if not messages:
            bot.reply_to(reply_to_bale_msg, "No messages found.")
            return

        for msg in reversed(messages):
            sender = await msg.get_sender()
            sender_name = get_display_name(sender) if sender else "Unknown"
            date = msg.date.strftime("%Y-%m-%d %H:%M:%S")
            link = None
            if hasattr(entity, 'username') and entity.username:
                link = f"https://t.me/{entity.username}/{msg.id}"
            elif hasattr(entity, 'id') and isinstance(entity.id, int) and entity.id < 0:
                channel_id = str(entity.id)[4:]
                link = f"https://t.me/c/{channel_id}/{msg.id}"

            formatted_text = format_with_links(msg.text or "", msg.entities) if msg.entities else (msg.text or "")
            media_info = ""
            has_media = False
            if msg.media:
                has_media = True
                media_type = "File"
                size = None
                if msg.document:
                    media_type = "Document"
                    if msg.document.attributes:
                        for attr in msg.document.attributes:
                            if hasattr(attr, 'file_name'):
                                media_type += f" {attr.file_name}"
                    size = msg.document.size
                elif msg.photo:
                    media_type = "Photo"
                    if msg.photo.sizes:
                        largest = msg.photo.sizes[-1]
                        size = largest.size if hasattr(largest, 'size') else None
                elif msg.video:
                    media_type = "Video"
                    size = msg.video.size
                elif msg.audio:
                    media_type = "Audio"
                    size = msg.audio.size
                elif msg.voice:
                    media_type = "Voice"
                    size = msg.voice.size
                if size:
                    size_mb = size / (1024 * 1024)
                    media_info = f"{media_type} ({size_mb:.1f} MB)"
                else:
                    media_info = media_type

            content = f"👤 {sender_name} • {date}\n"
            if formatted_text:
                content += formatted_text + "\n"
            if media_info:
                content += media_info + "\n"
            if link:
                content += f"🔗 {link}"

            markup = None
            if has_media:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("Download", callback_data="download_btn"))

            bale_msg = bot.send_message(reply_to_bale_msg.chat.id, content, reply_markup=markup)
            BALE_TO_TG[bale_msg.message_id] = (entity.id, msg.id)

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
                    inline_buttons = []
                    for idx, b in enumerate(button_list, start=1):
                        if b['type'] == 'callback':
                            cb_data = f"cb:{entity.id}:{msg.id}:{b['data']}"
                            inline_buttons.append(InlineKeyboardButton(str(idx), callback_data=cb_data))
                        else:
                            inline_buttons.append(InlineKeyboardButton(b['text'], url=b['url']))
                    rows = [inline_buttons[i:i+3] for i in range(0, len(inline_buttons), 3)]
                    markup_buttons = InlineKeyboardMarkup(rows)
                    list_text = "Buttons:\n" + format_buttons_list(button_list)
                    list_msg = bot.send_message(reply_to_bale_msg.chat.id, list_text, reply_markup=markup_buttons)
                    BUTTON_LISTS[list_msg.message_id] = (entity.id, msg.id, button_list)
    except Exception as e:
        bot.reply_to(reply_to_bale_msg, f"Error fetching history: {e}")

async def download_message(chat_id, msg_id, bale_message, progress_msg):
    file_path = None
    try:
        entity = await client.get_entity(chat_id)
        msg = await client.get_messages(entity, ids=msg_id)
        if not msg:
            bot.edit_message_text("Message not found.", bale_message.chat.id, progress_msg.message_id)
            return
        if not msg.media:
            bot.edit_message_text("No media in this message.", bale_message.chat.id, progress_msg.message_id)
            return

        file_size_mb = None
        if msg.document and msg.document.size:
            file_size_mb = msg.document.size / (1024 * 1024)
            bot.edit_message_text(f"Downloading file... ({file_size_mb:.1f} MB)",
                                  bale_message.chat.id, progress_msg.message_id)
        else:
            bot.edit_message_text("Downloading file...",
                                  bale_message.chat.id, progress_msg.message_id)

        file_path = await client.download_media(msg, file="downloads/")
        if not file_path:
            bot.edit_message_text("Failed to download file.", bale_message.chat.id, progress_msg.message_id)
            return

        size = os.path.getsize(file_path)
        bot.edit_message_text(f"Sending file ({size/(1024*1024):.1f} MB)...",
                              bale_message.chat.id, progress_msg.message_id)

        if size > CHUNK_SIZE:
            parts = split_file(file_path, CHUNK_SIZE)
            base_name = os.path.basename(file_path)
            base_no_ext, ext = os.path.splitext(base_name)
            for part in parts:
                with open(part, 'rb') as f:
                    bot.send_document(bale_message.chat.id, f, caption=f"{os.path.basename(part)}")
                os.remove(part)
            reassembly = f"cat {base_no_ext}.part*{ext} > {base_name}"
            bot.send_message(bale_message.chat.id,
                             f"File split into {len(parts)} parts. To reassemble:\n{reassembly}")
        else:
            with open(file_path, 'rb') as f:
                bot.send_document(bale_message.chat.id, f, caption=f"{os.path.basename(file_path)}")
        os.remove(file_path)
        file_path = None
        bot.delete_message(bale_message.chat.id, progress_msg.message_id)
    except Exception as e:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        bot.edit_message_text(f"Error downloading: {e}", bale_message.chat.id, progress_msg.message_id)

async def download_message_by_url(chat_part, msg_id, bale_message, progress_msg):
    try:
        if chat_part.isdigit():
            try:
                entity = await client.get_entity(int(chat_part))
            except:
                try:
                    entity = await client.get_entity(int(f"-100{chat_part}"))
                except:
                    raise Exception("Cannot find entity with that ID.")
        else:
            entity = await client.get_entity(f"@{chat_part}")
        await download_message(entity.id, msg_id, bale_message, progress_msg)
    except Exception as e:
        bot.edit_message_text(f"Error: {e}", bale_message.chat.id, progress_msg.message_id)

async def send_callback_answer(chat_id, message_id, callback_data, bale_message):
    try:
        entity = await client.get_entity(chat_id)
        result = await client(GetBotCallbackAnswerRequest(
            peer=entity,
            msg_id=message_id,
            data=callback_data.encode('utf-8')
        ))
        target = None
        if hasattr(entity, 'username') and entity.username:
            target = f"@{entity.username}"
        else:
            target = str(chat_id)
        if result.message:
            bot.send_message(bale_message.chat.id, f"{result.message}",
                             reply_markup=InlineKeyboardMarkup().add(
                                 InlineKeyboardButton("History (5)", callback_data=f"history:{target}:5")
                             ))
        elif result.alert:
            bot.send_message(bale_message.chat.id, f"{result.alert}",
                             reply_markup=InlineKeyboardMarkup().add(
                                 InlineKeyboardButton("History (5)", callback_data=f"history:{target}:5")
                             ))
        else:
            bot.send_message(bale_message.chat.id, "Callback sent.",
                             reply_markup=InlineKeyboardMarkup().add(
                                 InlineKeyboardButton("History (5)", callback_data=f"history:{target}:5")
                             ))
    except Exception as e:
        bot.send_message(bale_message.chat.id, f"Error sending callback: {e}")

@bot.callback_query_handler(func=lambda call: True)
def callback_query_handler(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Not authorized.")
        return

    data = call.data
    if data.startswith("history:"):
        parts = data.split(":", 2)
        if len(parts) >= 2:
            target = parts[1]
            count = 5
            if len(parts) == 3:
                try:
                    count = int(parts[2])
                except:
                    pass
            bot.answer_callback_query(call.id, f"Fetching last {count} messages...")
            asyncio.run_coroutine_threadsafe(
                get_history(target, count, call.message),
                tg_loop
            )
    elif data == "download_btn":
        bale_msg_id = call.message.message_id
        if bale_msg_id in BALE_TO_TG:
            chat_id, tg_msg_id = BALE_TO_TG[bale_msg_id]
            progress_msg = bot.send_message(call.message.chat.id, "Processing download...")
            asyncio.run_coroutine_threadsafe(
                download_message(chat_id, tg_msg_id, call.message, progress_msg),
                tg_loop
            )
        else:
            bot.answer_callback_query(call.id, "Cannot find original message.")
    elif data.startswith("cb:"):
        parts = data.split(":", 3)
        if len(parts) == 4:
            _, tg_chat_str, tg_msg_str, callback_data = parts
            try:
                tg_chat = int(tg_chat_str)
                tg_msg = int(tg_msg_str)
                asyncio.run_coroutine_threadsafe(
                    send_callback_answer(tg_chat, tg_msg, callback_data, call.message),
                    tg_loop
                )
            except Exception as e:
                bot.answer_callback_query(call.id, f"Error: {e}")
        else:
            bot.answer_callback_query(call.id, "Invalid callback data.")
    elif data.startswith("replykb:"):
        action_id = data.split(":", 1)[1]
        if action_id in REPLY_KB_ACTIONS:
            bot_entity, button_text = REPLY_KB_ACTIONS.pop(action_id)
            bot.answer_callback_query(call.id, f"Sending '{button_text}'...")
            asyncio.run_coroutine_threadsafe(
                send_reply_keyboard_press(bot_entity, button_text, call.message.chat.id),
                tg_loop
            )
        else:
            bot.answer_callback_query(call.id, "Action expired or invalid.")
    else:
        bot.answer_callback_query(call.id, "Unknown action.")

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
        bot.reply_to(message, "Reply sent.")

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

print("Bale bot started...")
bot.infinity_polling(skip_pending=True)