import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")

BASE_URL = f"http://tapi.bale.ai/bot{TOKEN}"

def get_updates(offset=None):
    url = f"{BASE_URL}/getUpdates"
    params = {}
    if offset:
        params['offset'] = offset
    response = requests.get(url, params=params)
    return response.json()


def send_file(chat_id, file_path, caption=None):
    url = f"{BASE_URL}/sendDocument"
    with open(file_path, "rb") as f:
        files = {
            "document": (os.path.basename(file_path), f)
        }
        data = {
            "chat_id": chat_id,
            "caption": caption or ""
        }
        response = requests.post(url, data=data, files=files)
    print("send file response:", response.status_code, response.text)


def run_bot():
    last_update_id = None
    print("Bot is running...")

    while True:
        updates = get_updates(last_update_id)
        if 'result' in updates:
            for update in updates['result']:
                message = update.get("message")
                if message:
                    chat_id = message["chat"]["id"]
                    text = message.get("text")
                    print("New message:", text)

                    if text == "/file":
                        send_file(chat_id, "echo.py", caption="this is my file!")

                last_update_id = update["update_id"] + 1

        time.sleep(1)


if __name__ == "__main__":
    run_bot()
