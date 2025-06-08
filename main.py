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
import re
import atexit

app = Flask(__name__)

channel_access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
channel_secret = os.environ.get('LINE_CHANNEL_SECRET')

configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

scheduler = BackgroundScheduler()
scheduler.start()
atexit.register(lambda: scheduler.shutdown())  # 確保應用退出時關閉排程器

def parse_reminder_text(text):
    """更可靠的提醒內容解析"""
    # 嘗試匹配日期時間格式
    dt_match = re.search(r'(\d{1,2}月\d{1,2}日 \d{1,2}:\d{2})', text)
    if not dt_match:
        return None, None
    
    dt_str = dt_match.group(1)
    dt = dateparser.parse(dt_str, languages=["zh"])
    if not dt:
        return None, None
    
    content = text.replace(dt_str, "").strip()
    return dt, content

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    user_id = event.source.user_id
    
    dt, content = parse_reminder_text(user_message)
    
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)

        if not dt or not content:
            messaging_api.reply_message(
                reply_token=event.reply_token,
                messages=[TextMessage(text="請輸入格式如「6月12日 15:30 看牙醫」")]
            )
            return

        now = datetime.now()
        if dt <= now:
            messaging_api.reply_message(
                reply_token=event.reply_token,
                messages=[TextMessage(text="這個時間已經過了，請重新輸入未來的時間。")]
            )
            return

        reminder_time = dt - timedelta(minutes=15)
        
        try:
            scheduler.add_job(
                send_reminder,
                'date',
                run_date=reminder_time,
                args=[user_id, content, dt.strftime("%Y-%m-%d %H:%M")],
                id=f"{user_id}_{dt.timestamp()}"  # 唯一ID防止重複任務
            )
            
            reply_text = f"已記下：{dt.strftime('%m月%d日 %H:%M')}「{content}」，我會提前15分鐘提醒你！"
            messaging_api.reply_message(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        except Exception as e:
            app.logger.error(f"Failed to schedule reminder: {str(e)}")
            messaging_api.reply_message(
                reply_token=event.reply_token,
                messages=[TextMessage(text="設定提醒時發生錯誤，請稍後再試")]
            )

def send_reminder(user_id, content, time_str):
    try:
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            messaging_api.push_message(
                to=user_id,
                messages=[TextMessage(text=f"提醒你：即將在 15 分鐘後「{content}」（{time_str}）")]
            )
    except Exception as e:
        app.logger.error(f"Failed to send reminder: {str(e)}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
