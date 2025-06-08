from flask import Flask, request, abort
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi
from linebot.v3.messaging import TextMessage
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError

from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import dateparser
import os

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
    dt = dateparser.parse(user_message, languages=["zh"])
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)

        if dt:
            now = datetime.now()
            if dt > now:
                # 把日期跟時間去掉，剩下是內容
                content = user_message.replace(str(dt.date()), "").replace(str(dt.time()), "").strip()
                reminder_time = dt - timedelta(minutes=15)

                reply_text = f"已記下：{dt.strftime('%m月%d日 %H:%M')}「{content}」，我會提前15分鐘提醒你！"
                messaging_api.reply_message(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )

                scheduler.add_job(send_reminder, 'date', run_date=reminder_time,
                                  args=[event.source.user_id, content, dt.strftime("%Y-%m-%d %H:%M")])
            else:
                messaging_api.reply_message(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="這個時間已經過了，請重新輸入未來的時間。")]
                )
        else:
            messaging_api.reply_message(
                reply_token=event.reply_token,
                messages=[TextMessage(text="請輸入格式如「6月12日 15:30 看牙醫」")]
            )

def send_reminder(user_id, content, time_str):
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.push_message(
            to=user_id,
            messages=[TextMessage(text=f"提醒你：即將在 15 分鐘後「{content}」（{time_str}）")]
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
