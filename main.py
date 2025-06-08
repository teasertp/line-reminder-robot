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
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Suppress less important logs
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('linebot').setLevel(logging.INFO)

def get_env(key):
    """Safely get environment variable"""
    value = os.getenv(key)
    if not value:
        logger.error(f"❌ Missing required environment variable: {key}")
        exit(1)
    return value

# Initialize Flask
app = Flask(__name__)

# LINE configuration
channel_access_token = get_env('LINE_CHANNEL_ACCESS_TOKEN')
channel_secret = get_env('LINE_CHANNEL_SECRET')
configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

def init_scheduler():
    """Initialize the scheduler with proper settings"""
    scheduler = BackgroundScheduler(
        daemon=True,
        job_defaults={
            'misfire_grace_time': 60*5,  # Allow 5 minutes delay
            'coalesce': True  # Combine multiple triggers
        }
    )
    scheduler.start()
    
    # Graceful shutdown
    def shutdown_scheduler():
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler has been shutdown")
    
    atexit.register(shutdown_scheduler)
    return scheduler

scheduler = init_scheduler()

def parse_reminder_text(text):
    """Parse Chinese datetime and advance time from message"""
    # Match datetime (supports both "6月8日 18:25" and "6月8日18:25")
    date_match = re.search(r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})', text)
    if not date_match:
        return None, None, None
    
    month, day, hour, minute = map(int, date_match.groups())
    
    # Handle year automatically
    now = datetime.now()
    year = now.year
    if (month, day) < (now.month, now.day):
        year += 1
    
    try:
        dt = datetime(year, month, day, hour, minute)
    except ValueError:
        return None, None, None

    # Extract advance time (supports "提前X分鐘")
    advance_match = re.search(r'提前(\d+)分鐘', text)
    advance_minutes = int(advance_match.group(1)) if advance_match else 15

    # Clean content
    content = re.sub(
        r'\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}|提前\d+分鐘', 
        '', 
        text
    ).strip()
    
    return dt, content, advance_minutes

@app.route("/callback", methods=['POST'])
def callback():
    """LINE webhook callback"""
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        abort(500)
        
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """Handle incoming messages"""
    try:
        user_id = event.source.user_id
        reply_token = event.reply_token
        user_message = event.message.text

        dt, content, advance_minutes = parse_reminder_text(user_message)
        
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)

            # Validate parsing result
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

            # Check if time is in future
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

            # Set up reminder job
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

            # Send confirmation
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
        logger.error(f"Message handling error: {e}")
        try:
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="⚠️ 處理您的消息時發生錯誤，請稍後再試")]
                    )
                )
        except Exception as fallback_error:
            logger.critical(f"Failed to send error reply: {fallback_error}")

def send_reminder(user_id, content, time_str, advance_minutes):
    """Send reminder message to user"""
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
        logger.error(f"Failed to send reminder: {e}")

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "components": {
            "line_api": bool(channel_access_token),
            "scheduler": scheduler.running,
            "jobs_count": len(scheduler.get_jobs())
        }
    }, 200

@app.route('/metrics')
def metrics():
    """Prometheus metrics endpoint"""
    from prometheus_client import generate_latest
    return generate_latest(), 200, {'Content-Type': 'text/plain'}

@app.route('/reminders', methods=['GET'])
def list_reminders():
    """List all scheduled reminders (for debugging)"""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time),
            "args": job.args
        })
    return {"jobs": jobs}, 200

@app.errorhandler(Exception)
def handle_global_error(e):
    """Global error handler"""
    logger.error(f"Global exception: {str(e)}", exc_info=True)
    return {"error": "Internal Server Error"}, 500

if __name__ == "__main__":
    from waitress import serve
    serve(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        threads=4
    )
