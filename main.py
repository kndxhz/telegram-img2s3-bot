from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import logging
import os
from dotenv import load_dotenv
from PIL import Image


load_dotenv()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
SOCKS_PROXY = os.getenv("SOCKS_PROXY")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

if not TG_BOT_TOKEN:
    raise RuntimeError("未设置 TG_BOT_TOKEN 环境变量，请在 .env 文件中设置。")


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    message = update.effective_message
    logger.info(
        "[%s][%s] %s",
        user.id if user else "unknown",
        getattr(user, "full_name", "unknown"),
        message.text if message else "",
    )
    if message is None:
        return

    if update.effective_chat and update.effective_chat.id == CHAT_ID:
        await message.reply_text("请发送图片文件，将进行压缩并上传到 S3。")
    else:
        await message.reply_text("你未取得授权使用此机器人，请联系管理员。")


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Exception while handling an update", exc_info=context.error)


def main():
    builder = ApplicationBuilder().token(TG_BOT_TOKEN)
    if SOCKS_PROXY:
        builder = builder.proxy(SOCKS_PROXY).get_updates_proxy(SOCKS_PROXY)

    application = builder.build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))
    # application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, start))
    application.add_error_handler(handle_error)

    application.run_polling()


if __name__ == "__main__":
    main()
