import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import boto3

load_dotenv()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
SOCKS_PROXY = os.getenv("SOCKS_PROXY")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
AWS_S3_HOST = os.getenv("AWS_S3_HOST")
AWS_ACCESS_ACCESS_KEY = os.getenv("AWS_ACCESS_ACCESS_KEY")
AWS_SECRET_SECRET_KEY = os.getenv("AWS_SECRET_SECRET_KEY")

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


async def process_image(update: Update, context: CallbackContext):
    message = update.effective_message
    if not message:
        return

    if update.effective_chat and update.effective_chat.id != CHAT_ID:
        await message.reply_text("你未取得授权使用此机器人，请联系管理员。")
        return

    # 创建目录
    Path("temp").mkdir(exist_ok=True)
    Path("processed").mkdir(exist_ok=True)

    if message.photo:
        photo = message.photo[-1]
        file = await photo.get_file()
        file_path = Path("temp") / f"{file.file_id}.jpg"
        await file.download_to_drive(file_path)

    elif message.document and (
        getattr(message.document, "mime_type", "") or ""
    ).startswith("image/"):
        file = await message.document.get_file()
        ext = Path(message.document.file_name or "").suffix or ".png"
        file_path = Path("temp") / f"{file.file_id}{ext}"
        await file.download_to_drive(file_path)

    else:
        await message.reply_text("请发送图片文件。")
        return

    # 转换为无损 WebP
    output_path = Path("processed") / f"{file.file_id}.webp"

    with Image.open(file_path) as img:
        # 保留透明通道
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

        img.save(
            output_path,
            format="WEBP",
            lossless=True,  # 无损压缩
            method=6,  # 最大压缩率（0~6）
        )

    await compress_and_upload(str(output_path), update, context)


async def compress_and_upload(
    output_path: str, update: Update, context: CallbackContext
):
    message = update.effective_message
    if message:
        await message.reply_text(f"已处理并保存：{output_path}")


def main():
    builder = ApplicationBuilder().token(TG_BOT_TOKEN)
    if SOCKS_PROXY:
        builder = builder.proxy(SOCKS_PROXY).get_updates_proxy(SOCKS_PROXY)

    application = builder.build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, process_image)
    )
    application.add_error_handler(handle_error)

    application.run_polling()


if __name__ == "__main__":
    main()
