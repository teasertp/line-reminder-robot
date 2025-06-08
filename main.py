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
from apscheduler.jobstores.memory import MemoryJobStore
import os
import re
import atexit
import logging
import pytz

# 初始化設定
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LINE 設定
channel_access_token = os.environ['pR4jAkUhh3ovttVfxbuo5G+jPdCtEnUIAawh3VWT0Lznm2zFISBSrTASGKCV4DctsWYv/aXaFMiVj4BQEHCVAFfXz6hkSmi8bRx1ZtbqNla4FVtfHVFu47S7R10ZkvlZA5mBrwj5/Jgxp61o4fHs5gdB04t89/1O/w1cDnyilFU=']
channel_secret = os.environ['ab31f1bde2bc61ce3acb2a0f5ceaf186']
configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

# 時區設定 (台北時區)
tz = pytz.timezone('Asia/Taipei')

# 排程器設定 (使用記憶體儲存 + 時區設定)
jobstores = {
    'default': MemoryJobStore()
}
scheduler = BackgroundScheduler(
    jobstores=jobstores,
    timezone=tz  # 重要！設定時區
)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

def parse_reminder_text(text):
    """解析包含中文日期和提前時間的訊息"""
    # 匹配日期時間 (支援「6月8日 18:25」和「6月8日18:25」)
    date_match = re.search(r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})', text)
    if not date_match:
        return None, None, None
    
    month, day, hour, minute = map(int, date_match.groups())
    
    # 自動處理年份 (考慮跨年)
    now = datetime.now(tz)
    year = now.year
    if (month, day) < (now.month, now.day):
        year += 1
    
    try:
        dt = tz.localize(datetime(year, month, day, hour, minute))
    except ValueError:
        return None, None, None

    # 提取提前時間 (支援「提前X分鐘」)
    advance_match = re.search(r'提前(\d+)分鐘', text)
    advance_minutes = int(advance_match.group(1)) if advance_match else 15

    # 清理事件內容 (移除日期和提前時間部分)
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
        logger.info(f"收到請求: {body[:200]}...")  # 日誌記錄原始請求
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("簽章驗證失敗")
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
        logger.info(f"用戶 {user_id} 發送訊息: {user_message}")

        dt, content, advance_minutes = parse_reminder_text(user_message)
        
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)

            # 驗證解析結果
            if not dt or not content:
                logger.warning(f"無法解析訊息: {user_message}")
                line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(
                            text="📝 請使用格式：\n"
                                 "「6月12日 15:30 看牙醫」\n"
                                 "或\n"
                                 "「6月12日15:30看牙醫 提前20分鐘提醒」"
                        )]
                    )
                )
                return

            # 檢查是否為未來時間
            now = datetime.now(tz)
            if dt <= now:
                logger.warning(f"時間已過期: {dt} (現在: {now})")
                line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(
                            text="⏰ 請輸入未來的時間！\n"
                                 "(您輸入的時間已過期)"
                        )]
                    )
                )
                return

            # 計算提醒時間
            reminder_time = dt - timedelta(minutes=advance_minutes)
            job_id = f"reminder_{user_id}_{int(dt.timestamp())}"
            
            # 添加排程任務
            scheduler.add_job(
                send_reminder,
                'date',
                run_date=reminder_time,
                args=[user_id, content, dt.strftime("%m/%d %H:%M"), advance_minutes],
                id=job_id,
                replace_existing=True
            )
            logger.info(f"已排程: 將於 {reminder_time} 發送提醒 (ID: {job_id})")

            # 回覆確認訊息
            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(
                        text=f"✅ 提醒設定成功！\n"
                             f"──────────────\n"
                             f"▪ 時間：{dt.strftime('%m月%d日 %H:%M')}\n"
                             f"▪ 事項：{content}\n"
                             f"▪ 提前 {advance_minutes} 分鐘通知\n"
                             f"──────────────\n"
                             f"到期前會收到提醒訊息"
                    )]
                )
            )

    except Exception as e:
        logger.error(f"處理訊息時發生錯誤: {e}", exc_info=True)

def send_reminder(user_id, content, time_str, advance_minutes):
    try:
        logger.info(f"準備發送提醒給 {user_id}: {content} 於 {time_str}")
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)
            line_api.push_message(
                to=user_id,
                messages=[TextMessage(
                    text=f"⏰ 提醒通知！\n"
                         f"──────────────\n"
                         f"您設定的行程即將在 {advance_minutes} 分鐘後開始：\n"
                         f"▪ 時間：{time_str}\n"
                         f"▪ 事項：{content}\n"
                         f"──────────────\n"
                         f"請準時參加！"
                )]
            )
        logger.info("提醒發送成功")
    except Exception as e:
        logger.error(f"發送提醒失敗: {e}", exc_info=True)

if __name__ == "__main__":
    # 啟動時檢查排程器
    logger.info("應用程式啟動中...")
    logger.info(f"當前時區: {tz}")
    logger.info(f"當前時間: {datetime.now(tz)}")
    logger.info(f"已排程任務: {len(scheduler.get_jobs())} 個")
    
    # 啟動測試排程 (2分鐘後執行)
    test_time = datetime.now(tz) + timedelta(minutes=2)
    scheduler.add_job(
        lambda: logger.info("*** 測試排程執行成功 ***"),
        'date',
        run_date=test_time,
        id='test_job'
    )
    logger.info(f"已添加測試排程，將於 {test_time} 執行")
    
    # 啟動伺服器
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
