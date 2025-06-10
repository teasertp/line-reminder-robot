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

# ==================== 基础配置 ====================
# 初始化Flask应用
app = Flask(__name__)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),  # 日志文件
        logging.StreamHandler()          # 控制台输出
    ]
)
logger = logging.getLogger(__name__)

# 减少不重要的日志输出
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('linebot').setLevel(logging.INFO)

# ==================== 环境变量处理 ====================
def 获取环境变量(变量名):
    """安全获取环境变量，如果不存在则退出程序"""
    值 = os.getenv(变量名)
    if not 值:
        logger.error(f"❌ 缺少必要的环境变量: {变量名}")
        exit(1)
    return 值

# LINE机器人配置
LINE_TOKEN = 获取环境变量('LINE_CHANNEL_ACCESS_TOKEN')  # LINE频道访问令牌
LINE_SECRET = 获取环境变量('LINE_CHANNEL_SECRET')      # LINE频道密钥
line配置 = Configuration(access_token=LINE_TOKEN)
消息处理器 = WebhookHandler(LINE_SECRET)

# ==================== 定时任务调度器 ====================
def 初始化调度器():
    """创建并配置定时任务调度器"""
    调度器 = BackgroundScheduler(
        daemon=True,
        job_defaults={
            'misfire_grace_time': 60*5,  # 允许5分钟内的延迟执行
            'coalesce': True             # 合并多次触发
        }
    )
    调度器.start()
    
    # 优雅关闭处理
    def 关闭调度器():
        if 调度器.running:
            调度器.shutdown(wait=False)
            logger.info("定时任务调度器已关闭")
    
    atexit.register(关闭调度器)
    return 调度器

任务调度器 = 初始化调度器()

# ==================== 核心功能函数 ====================
def 解析提醒内容(文本):
    """
    从用户消息中解析出日期时间、提醒内容和提前时间
    支持格式：
    - "6月8日 18:25 开会"
    - "6月8日18:25开会 提前20分钟"
    """
    # 匹配中文日期时间
    日期匹配 = re.search(r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})', 文本)
    if not 日期匹配:
        return None, None, None
    
    月, 日, 时, 分 = map(int, 日期匹配.groups())
    
    # 自动处理年份（如果月份日期已过，则设为明年）
    现在 = datetime.now()
    年 = 现在.year
    if (月, 日) < (现在.month, 现在.day):
        年 += 1
    
    try:
        日期时间 = datetime(年, 月, 日, 时, 分)
    except ValueError:
        return None, None, None

    # 提取提前时间（默认15分钟）
    提前匹配 = re.search(r'提前(\d+)分鐘', 文本)
    提前分钟 = int(提前匹配.group(1)) if 提前匹配 else 15

    # 清理提醒内容（移除日期和提前时间部分）
    内容 = re.sub(
        r'\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}|提前\d+分鐘', 
        '', 
        文本
    ).strip()
    
    return 日期时间, 内容, 提前分钟

def 发送提醒(用户ID, 内容, 时间字符串, 提前分钟):
    """实际发送提醒消息给用户"""
    try:
        with ApiClient(line配置) as api客户端:
            line接口 = MessagingApi(api客户端)
            line接口.push_message(
                to=用户ID,
                messages=[TextMessage(
                    text=f"⏰ 提醒通知：\n"
                         f"您將在 {提前分钟} 分鐘後 ({时间字符串})\n"
                         f"有行程：「{内容}」"
                )]
            )
    except Exception as 错误:
        logger.error(f"發送提醒失敗: {错误}")

# ==================== LINE消息处理 ====================
@app.route("/callback", methods=['POST'])
def 回调处理():
    """LINE平台的消息回调接口"""
    # 验证签名
    签名 = request.headers.get('X-Line-Signature', '')
    请求体 = request.get_data(as_text=True)
    
    try:
        消息处理器.handle(请求体, 签名)
    except InvalidSignatureError:
        abort(400)
    except Exception as 错误:
        logger.error(f"Webhook處理錯誤: {错误}")
        abort(500)
        
    return 'OK'

@消息处理器.add(MessageEvent, message=TextMessageContent)
def 处理用户消息(事件):
    """处理用户发送的文本消息"""
    try:
        用户ID = 事件.source.user_id
        回复令牌 = 事件.reply_token
        用户消息 = 事件.message.text

        # 解析用户输入
        提醒时间, 提醒内容, 提前分钟 = 解析提醒内容(用户消息)
        
        with ApiClient(line配置) as api客户端:
            line接口 = MessagingApi(api客户端)

            # 验证解析结果
            if not 提醒时间 or not 提醒内容:
                line接口.reply_message(
                    ReplyMessageRequest(
                        reply_token=回复令牌,
                        messages=[TextMessage(
                            text="請輸入正確格式：\n「6月12日 15:30 會議」\n"
                                 "或\n"
                                 "「6月12日15:30會議 提前20分鐘」"
                        )]
                    )
                )
                return

            # 检查是否为未来时间
            if 提醒时间 <= datetime.now():
                line接口.reply_message(
                    ReplyMessageRequest(
                        reply_token=回复令牌,
                        messages=[TextMessage(
                            text="請輸入未來的時間！\n"
                                 "（您輸入的時間已經過期）"
                        )]
                    )
                )
                return

            # 计算实际提醒时间（提前X分钟）
            实际提醒时间 = 提醒时间 - timedelta(minutes=提前分钟)
            任务ID = f"reminder_{用户ID}_{提醒时间.timestamp()}"

            # 添加定时任务
            任务调度器.add_job(
                发送提醒,
                'date',
                run_date=实际提醒时间,
                args=[用户ID, 提醒内容, 提醒时间.strftime("%m/%d %H:%M"), 提前分钟],
                id=任务ID,
                replace_existing=True
            )

            # 发送确认消息
            line接口.reply_message(
                ReplyMessageRequest(
                    reply_token=回复令牌,
                    messages=[TextMessage(
                        text=f"✅ 已設定提醒：\n"
                             f"時間：{提醒时间.strftime('%m月%d日 %H:%M')}\n"
                             f"事項：{提醒内容}\n"
                             f"將提前 {提前分钟} 分鐘通知您"
                    )]
                )
            )

    except Exception as 错误:
        logger.error(f"處理消息時錯誤: {错误}")
        try:
            with ApiClient(line配置) as api客户端:
                MessagingApi(api客户端).reply_message(
                    ReplyMessageRequest(
                        reply_token=事件.reply_token,
                        messages=[TextMessage(text="⚠️ 處理您的消息時發生錯誤，請稍後再試")]
                    )
                )
        except Exception as 错误:
            logger.critical(f"連錯誤回復都失敗了: {错误}")

# ==================== 管理接口 ====================
@app.route('/health')
def 健康检查():
    """健康检查接口"""
    return {
        "status": "正常",
        "timestamp": datetime.now().isoformat(),
        "components": {
            "line_api": bool(LINE_TOKEN),
            "scheduler": 任务调度器.running,
            "active_jobs": len(任务调度器.get_jobs())
        }
    }, 200

@app.route('/reminders')
def 查看所有提醒():
    """查看当前所有定时任务（调试用）"""
    任务列表 = []
    for 任务 in 任务调度器.get_jobs():
        任务列表.append({
            "id": 任务.id,
            "next_run": str(任务.next_run_time),
            "user_id": 任务.args[0] if len(任务.args) > 0 else None,
            "content": 任务.args[1] if len(任务.args) > 1 else None
        })
    return {"reminders": 任务列表}, 200

# ==================== 错误处理 ====================
@app.errorhandler(Exception)
def 全局错误处理(错误):
    """全局异常捕获"""
    logger.error(f"全局異常: {str(错误)}", exc_info=True)
    return {"error": "伺服器內部錯誤"}, 500

# ==================== 启动应用 ====================
if __name__ == "__main__":
    logger.info("啟動LINE提醒機器人...")
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),  # 使用环境变量或默认5000端口
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true"  # 根据环境变量决定调试模式
    )
