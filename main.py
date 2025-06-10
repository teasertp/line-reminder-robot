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
import pytz

# ==================== 基础配置 ====================
app = Flask(__name__)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 减少不重要的日志输出
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('linebot').setLevel(logging.INFO)

# ==================== 环境变量处理 ====================
def get_env_variable(name):
    value = os.getenv(name)
    if not value:
        logger.error(f"❌ Missing required environment variable: {name}")
        exit(1)
    return value

LINE_TOKEN = get_env_variable('LINE_CHANNEL_ACCESS_TOKEN')
LINE_SECRET = get_env_variable('LINE_CHANNEL_SECRET')
line_config = Configuration(access_token=LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# ==================== 定时任务调度器 ====================
def init_scheduler():
    """初始化非守护模式的调度器"""
    scheduler = BackgroundScheduler(
        timezone=pytz.timezone('Asia/Taipei'),  # 设置时区
        job_defaults={
            'misfire_grace_time': 300,  # 5分钟容错
            'coalesce': True
        }
    )
    scheduler.start()
    
    # 确保调度器在退出时正确关闭
    def shutdown_scheduler():
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler has been shut down")
    
    atexit.register(shutdown_scheduler)
    return scheduler

scheduler = init_scheduler()

# ==================== 核心功能 ====================
def parse_reminder(text):
    """解析提醒内容"""
    time_match = re.search(r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})', text)
    if not time_match:
        return None, None, None
    
    month, day, hour, minute = map(int, time_match.groups())
    now = datetime.now()
    year = now.year
    
    # 处理跨年情况
    if (month, day) < (now.month, now.day):
        year += 1
    
    try:
        reminder_time = datetime(year, month, day, hour, minute)
    except ValueError:
        return None, None, None

    # 提取提前时间
    advance_match = re.search(r'提前(\d+)分鐘', text)
    advance_minutes = int(advance_match.group(1)) if advance_match else 15

    # 清理内容
    content = re.sub(
        r'\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}|提前\d+分鐘', 
        '', 
        text
    ).strip()
    
    return reminder_time, content, advance_minutes

def send_reminder(user_id, content, time_str, advance_minutes):
    """发送提醒"""
    try:
        with ApiClient(line_config) as api_client:
            line_api = MessagingApi(api_client)
            line_api.push_message(
                to=user_id,
                messages=[TextMessage(
                    text=f"⏰ 提醒通知：\n"
                         f"您將在 {advance_minutes} 分鐘後 ({time_str})\n"
                         f"有行程：「{content}」"
                )]
            )
        logger.info(f"成功发送提醒给用户 {user_id}")
    except Exception as e:
        logger.error(f"发送提醒失败: {e}")

# ==================== LINE消息处理 ====================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        logger.error(f"Webhook处理错误: {e}")
        abort(500)
        
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_id = event.source.user_id
        reply_token = event.reply_token
        message_text = event.message.text

        # 解析用户输入
        reminder_time, content, advance_minutes = parse_reminder(message_text)
        
        with ApiClient(line_config) as api_client:
            line_api = MessagingApi(api_client)

            if not reminder_time or not content:
                line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(
                            text="請輸入正確格式：\n「6月12日 15:30 會議」\n"
                                 "或\n"
                                 "「6月12日15:30會議 提前20分鐘」"
                        )]
                    )
                )
                return

            if reminder_time <= datetime.now():
                line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(
                            text="請輸入未來的時間！\n"
                                 "（您輸入的時間已經過期）"
                        )]
                    )
                )
                return

            # 计算实际提醒时间
            actual_remind_time = reminder_time - timedelta(minutes=advance_minutes)
            job_id = f"reminder_{user_id}_{reminder_time.timestamp()}"

            # 添加定时任务
            scheduler.add_job(
                send_reminder,
                'date',
                run_date=actual_remind_time,
                args=[user_id, content, reminder_time.strftime("%m/%d %H:%M"), advance_minutes],
                id=job_id,
                replace_existing=True
            )

            logger.info(f"已为用户 {user_id} 创建提醒任务: {job_id}")

            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(
                        text=f"✅ 已設定提醒：\n"
                             f"時間：{reminder_time.strftime('%m月%d日 %H:%M')}\n"
                             f"事項：{content}\n"
                             f"將提前 {advance_minutes} 分鐘通知您"
                    )]
                )
            )

    except Exception as e:
        logger.error(f"处理消息时错误: {e}")
        try:
            with ApiClient(line_config) as api_client:
                MessagingApi(api_client).reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="⚠️ 處理您的消息時發生錯誤，請稍後再試")]
                    )
                )
        except Exception as e:
            logger.critical(f"連錯誤回復都失敗了: {e}")

# ==================== 管理接口 ====================
@app.route('/health')
def health_check():
    return {
        "status": "running",
        "scheduler": scheduler.running,
        "jobs_count": len(scheduler.get_jobs())
    }, 200

# ==================== 启动应用 ====================
if __name__ == "__main__":
    logger.info("Starting LINE Reminder Bot...")
    try:
        # 打印当前所有任务
        logger.info(f"Current jobs: {[job.id for job in scheduler.get_jobs()]}")
        app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", "5000")),
            debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
            use_reloader=False  # 禁用reloader避免重复启动调度器
        )
    except Exception as e:
        logger.error(f"Application failed: {e}")
        scheduler.shutdown()
