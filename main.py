from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os
import re
import atexit
import logging

# 初始化設定
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LINE 設定
channel_access_token = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
channel_secret = os.environ['LINE_CHANNEL_SECRET']
configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

# 排程器設定
scheduler = BackgroundScheduler(daemon=True)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

def parse_reminder_text(text):
    """解析包含中文日期和提前時間的訊息"""
    # 匹配日期時間 (支援「6月8日 18:25」和「6月8日18:25」)
    date_match = re.search(r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})', text)
    if not date_match:
        return None, None, None
    
    month, day, hour, minute = map(int, date_match.groups())
    
    # 自動處理年份
    now = datetime.now()
    year = now.year
    if (month, day) < (now.month, now.day):
        year += 1
    
    try:
        dt = datetime(year, month, day, hour, minute)
    except ValueError:
        return None, None, None

    # 提取提前時間 (支援「提前X分鐘」)
    advance_match = re.search(r'提前(\d+)分鐘', text)
    advance_minutes = int(advance_match.group(1)) if advance_match else 15

    # 清理事件內容
    content = re.sub(
        r'\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}|提前\d+分鐘', 
        '', 
        text
    ).strip()
    
    return dt, content, advance_minutes

@app.route("/callback", methods=['POST'])
def callback():
    # 驗證簽章
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        logger.error(f"Webhook 處理錯誤: {e}")
        abort(500)
        
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_id = event.source.user_id
        reply_token = event.reply_token
        user_message = event.message.text

        dt, content, advance_minutes = parse_reminder_text(user_message)
        
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)

            # 驗證解析結果
            if not dt or not content:
                line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(
                            text="請輸入：\n「6月12日 15:30 事件內容」\n"
                                 "或\n"
                                 "「6月12日15:30事件內容 提前20分鐘提醒」"
                        )]
                    )
                )
                return

            # 檢查是否為未來時間
            if dt <= datetime.now():
                line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(
                            text="請輸入未來的時間！\n"
                                 "（收到的時間已過期）"
                        )]
                    )
                )
                return

            # 設定提醒任務
            reminder_time = dt - timedelta(minutes=advance_minutes)
            job_id = f"reminder_{user_id}_{dt.timestamp()}"

            scheduler.add_job(
                send_reminder,
                'date',
                run_date=reminder_time,
                args=[user_id, content, dt.strftime("%m/%d %H:%M"), advance_minutes],
                id=job_id,
                replace_existing=True
            )

            # 回覆確認訊息
            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(
                        text=f"✅ 已設定提醒：\n"
                             f"時間：{dt.strftime('%m月%d日 %H:%M')}\n"
                             f"事項：{content}\n"
                             f"將提前 {advance_minutes} 分鐘通知您"
                    )]
                )
            )

    except Exception as e:
        logger.error(f"處理訊息時錯誤: {e}")

def send_reminder(user_id, content, time_str, advance_minutes):
    try:
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)
            line_api.push_message(
                to=user_id,
                messages=[TextMessage(
                    text=f"⏰ 提醒通知：\n"
                         f"您將在 {advance_minutes} 分鐘後 ({time_str})\n"
                         f"有行程：「{content}」"
                )]
            )
    except Exception as e:
        logger.error(f"發送提醒失敗: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000))) 我需要放入什麼自己的東西嗎
