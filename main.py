from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage, FlexContainer
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
import os
import re
import atexit
import logging
import json
from typing import Tuple, Optional

# 初始化設定
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LINE 設定
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
channel_secret = os.getenv('LINE_CHANNEL_SECRET')
configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

# 排程器設定
jobstores = {
    'default': MemoryJobStore()
}
scheduler = BackgroundScheduler(jobstores=jobstores, daemon=True)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# 使用者資料暫存 (實際應用應改用資料庫)
user_reminders = {}

def parse_reminder_text(text: str) -> Tuple[Optional[datetime], Optional[str], int]:
    """解析包含中文日期和提前時間的訊息"""
    try:
        # 增強的時間解析 (支援更多格式)
        patterns = [
            r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})',  # 6月8日 18:25
            r'(\d{1,2})[/-](\d{1,2})[/-]\s*(\d{1,2}):(\d{2})',  # 6/8 18:25
            r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})'  # 2023年6月8日 18:25
        ]
        
        dt = None
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                if len(groups) == 4:  # 沒有年份的格式
                    month, day, hour, minute = map(int, groups)
                    year = datetime.now().year
                    # 如果日期已過，自動加一年
                    if (month, day) < (datetime.now().month, datetime.now().day):
                        year += 1
                else:  # 有年份的格式
                    year, month, day, hour, minute = map(int, groups)
                
                dt = datetime(year, month, day, hour, minute)
                break

        if not dt:
            return None, None, 0

        # 提取提前時間 (支援更多表達方式)
        advance_match = re.search(r'(提前|提早|前)(\d+)(分鐘|分|min)', text)
        advance_minutes = int(advance_match.group(2)) if advance_match else 15

        # 清理事件內容
        content = re.sub(
            r'\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}|提前\d+分鐘|提醒我|提醒',
            '', 
            text
        ).strip()
        
        return dt, content, advance_minutes

    except Exception as e:
        logger.error(f"解析時間錯誤: {e}")
        return None, None, 0

def create_reminder_flex_message(reminders):
    """建立提醒清單的 Flex Message"""
    contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "您的提醒清單",
                    "weight": "bold",
                    "size": "xl",
                    "align": "center"
                },
                {
                    "type": "separator",
                    "margin": "md"
                }
            ]
        }
    }

    for job_id, reminder in reminders.items():
        contents["body"]["contents"].extend([
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "contents": [
                    {
                        "type": "text",
                        "text": f"⏰ {reminder['time']}",
                        "weight": "bold",
                        "size": "md"
                    },
                    {
                        "type": "text",
                        "text": f"事項: {reminder['content']}",
                        "size": "sm",
                        "margin": "sm"
                    },
                    {
                        "type": "text",
                        "text": f"提前 {reminder['advance']} 分鐘提醒",
                        "size": "xs",
                        "color": "#AAAAAA"
                    },
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "刪除提醒",
                            "data": f"delete_{job_id}"
                        },
                        "style": "primary",
                        "height": "sm",
                        "margin": "md"
                    }
                ]
            },
            {
                "type": "separator",
                "margin": "md"
            }
        ])

    if not reminders:
        contents["body"]["contents"].append({
            "type": "text",
            "text": "目前沒有設定任何提醒",
            "align": "center",
            "margin": "md"
        })

    return FlexMessage(alt_text="您的提醒清單", contents=FlexContainer.from_dict(contents))

@app.route("/callback", methods=['POST'])
def callback():
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
        user_message = event.message.text.strip()

        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)

            # 檢查是否為查看提醒指令
            if user_message.lower() in ["查看提醒", "我的提醒", "list"]:
                reminders = {
                    job.id: {
                        "time": job.args[2],
                        "content": job.args[1],
                        "advance": job.args[3]
                    }
                    for job in scheduler.get_jobs()
                    if job.id.startswith(f"reminder_{user_id}_")
                }
                
                if not reminders:
                    line_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=reply_token,
                            messages=[TextMessage(text="您目前沒有設定任何提醒")]
                        )
                    )
                else:
                    flex_message = create_reminder_flex_message(reminders)
                    line_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=reply_token,
                            messages=[flex_message]
                        )
                    )
                return

            # 檢查是否為刪除所有提醒指令
            if user_message.lower() in ["清除所有提醒", "delete all"]:
                count = 0
                for job in scheduler.get_jobs():
                    if job.id.startswith(f"reminder_{user_id}_"):
                        scheduler.remove_job(job.id)
                        count += 1
                
                line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(
                            text=f"已刪除 {count} 個提醒" if count > 0 
                            else "沒有可刪除的提醒"
                        )]
                    )
                )
                return

            # 解析提醒訊息
            dt, content, advance_minutes = parse_reminder_text(user_message)
            
            if not dt or not content:
                line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(
                            text="📝 請使用以下格式設定提醒：\n"
                                 "「6月12日 15:30 開會」\n"
                                 "或\n"
                                 "「2023/6/12 15:30 開會 提前10分鐘提醒」\n\n"
                                 "其他指令：\n"
                                 "「查看提醒」 - 列出所有提醒\n"
                                 "「清除所有提醒」 - 刪除所有提醒"
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
                            text="⚠️ 請輸入未來的時間！\n"
                                 "（您輸入的時間已經過去）"
                        )]
                    )
                )
                return

            # 設定提醒任務
            reminder_time = dt - timedelta(minutes=advance_minutes)
            job_id = f"reminder_{user_id}_{dt.timestamp()}"
            
            # 儲存使用者提醒資料
            user_reminders[job_id] = {
                "user_id": user_id,
                "content": content,
                "time": dt.strftime("%m/%d %H:%M"),
                "advance": advance_minutes
            }

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
                        text=f"✅ 提醒設定成功！\n"
                             f"⏰ 時間：{dt.strftime('%Y年%m月%d日 %H:%M')}\n"
                             f"📝 事項：{content}\n"
                             f"⏳ 將提前 {advance_minutes} 分鐘通知您\n\n"
                             f"輸入「查看提醒」可管理您的提醒"
                    )]
                )
            )

    except Exception as e:
        logger.error(f"處理訊息時錯誤: {e}", exc_info=True)
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="發生錯誤，請稍後再試")]
                )
            )

def send_reminder(user_id: str, content: str, time_str: str, advance_minutes: int):
    """發送提醒訊息"""
    try:
        logger.info(f"準備發送提醒給 {user_id}: {content}")
        
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)
            
            # 建立更美觀的提醒訊息
            reminder_text = (
                f"⏰ 提醒通知 ⏰\n"
                f"──────────────\n"
                f"您設定的行程即將開始！\n"
                f"🕒 時間：{time_str}\n"
                f"（{advance_minutes}分鐘後）\n"
                f"📌 事項：{content}\n"
                f"──────────────\n"
                f"需要重新設定請輸入「提醒我...」"
            )
            
            line_api.push_message(
                to=user_id,
                messages=[TextMessage(text=reminder_text)]
            )
            
            logger.info(f"已發送提醒給 {user_id}")

    except Exception as e:
        logger.error(f"發送提醒失敗: {str(e)}", exc_info=True)
        # 嘗試重新發送或記錄失敗

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
