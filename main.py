from flask import Flask, request, abort
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, TextMessage
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError

from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import dateparser
import os
import re

app = Flask(__name__)

channel_access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
channel_secret = os.environ.get('LINE_CHANNEL_SECRET')

configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

scheduler = BackgroundScheduler()
scheduler.start()

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text

    # 嘗試從訊息抓提前幾分鐘，沒抓到預設15分鐘
    match = re.search(r"提前(\d+)分鐘", user_message)
    remind_minutes = int(match.group(1)) if match else 15

    # 去掉「提前XX分鐘提醒」字串，避免影響時間解析
    clean_message = re.sub(r"提前\d+分鐘提醒", "", user_message).strip()

    dt = dateparser.parse(clean_message, languages=["zh"])

    if dt:
        now = datetime.now()
        if dt > now:
            # 去掉時間字串，剩下是提醒內容
            content = clean_message.replace(str(dt.date()), "").replace(str(dt.time()), "").strip()

            reminder_time = dt - timedelta(minutes=remind_minutes)

            with ApiClient(configuration) as api_client:
                messaging_api = MessagingApi(api_client)
                messaging_api.reply_message(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(text=f"已記下：{dt.strftime('%m月%d日 %H:%M')}「{content}」，我會提前{remind_minutes}分鐘提醒你！")
                    ]
                )

            scheduler.add_job(send_reminder, 'date', run_date=reminder_time,
                              args=[event.source.user_id, content, dt.strftime("%Y-%m-%d %H:%M")])
        else:
            with ApiClient(configuration) as api_client:
                messaging_api = MessagingApi(api_client)
                messaging_api.reply_message(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(text="這個時間已經過了，請重新輸入未來的時間。")
                    ]
                )
    else:
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            messaging_api.reply_message(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(text="請輸入格式如「6月12日 15:30 看牙醫 提前30分鐘提醒」")
                ]
            )

def send_reminder(user_id, content, time_str):
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.push_message(
            to=user_id,
            messages=[
                TextMessage(text=f"提醒你：即將在 {time_str}「{content}」")
            ]
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
