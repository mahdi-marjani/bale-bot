import requests
import time

TOKEN = "..."
BASE_URL = f"https://tapi.bale.ai/bot{TOKEN}"

def send_message(chat_id, text):
    url = f"{BASE_URL}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text
    }
    response = requests.post(url, json=data, verify=False)
    print("send response:", response.status_code, response.text)

def get_updates(offset=None):
    url = f"{BASE_URL}/getUpdates"
    params = {}
    if offset:
        params['offset'] = offset
    response = requests.get(url, params=params, verify=False)
    return response.json()

def run_bot():
    last_update_id = None
    print("Bot is running...")

    while True:
        updates = get_updates(last_update_id)
        if 'result' in updates:
            for update in updates['result']:
                print("New message:", update)
                message = update.get("message")
                if message:
                    chat_id = message["chat"]["id"]
                    text = message.get("text", "")
                    send_message(chat_id, f"📩 You said: {text}")
                last_update_id = update["update_id"] + 1
        time.sleep(1)

if __name__ == "__main__":
    run_bot()
