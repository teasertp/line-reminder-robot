@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.reply_message(
            reply_token=event.reply_token,
            messages=[TextMessage(text="測試回覆，確保格式沒問題")]
        )
