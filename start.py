import telebot
import telebot.apihelper as apihelper
import os
from dotenv import load_dotenv


load_dotenv()

apihelper.API_URL = "https://tapi.bale.ai/bot{0}/{1}"

TOKEN = os.getenv("TOKEN")

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=["start"])
def start(msg):
    bot.reply_to(msg, "hi")

bot.infinity_polling(skip_pending=True)
