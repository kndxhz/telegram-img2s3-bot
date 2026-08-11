import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import quote, urljoin

import boto3
from botocore.exceptions import BotoCoreError, ClientError
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

load_dotenv()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
SOCKS_PROXY = os.getenv("SOCKS_PROXY")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
AWS_S3_HOST = os.getenv("AWS_S3_HOST")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET")
AWS_S3_PUBLIC_URL = os.getenv("AWS_S3_PUBLIC_URL")
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
CONCURRENT_UPDATES = 8
MEDIA_GROUP_COLLECT_SECONDS = 1


async def reply_text(message, text: str, **kwargs):
    await message.reply_text(text, do_quote=True, **kwargs)


def s3_config_missing() -> bool:
    return bool(
        not AWS_S3_HOST
        or not AWS_S3_BUCKET
        or not AWS_ACCESS_ACCESS_KEY
        or not AWS_SECRET_SECRET_KEY
    )


def build_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=AWS_S3_HOST,
        aws_access_key_id=AWS_ACCESS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_SECRET_KEY,
    )


def save_lossless_webp(input_path: Path, output_path: Path):
    with Image.open(input_path) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

        img.save(
            output_path,
            format="WEBP",
            lossless=True,
            method=6,
        )


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
        await reply_text(message, "请发送图片文件，将进行压缩并上传到 S3。")
    else:
        await reply_text(message, "你未取得授权使用此机器人，请联系管理员。")


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Exception while handling an update", exc_info=context.error)


async def process_image(update: Update, context: CallbackContext):
    message = update.effective_message
    logger.info(
        "[%s][%s] %s",
        update.effective_user.id if update.effective_user else "unknown",
        getattr(update.effective_user, "full_name", "unknown"),
        message.text if message else "",
    )
    if not message:
        return

    if update.effective_chat and update.effective_chat.id != CHAT_ID:
        await reply_text(message, "你未取得授权使用此机器人，请联系管理员。")
        return

    if message.media_group_id:
        await queue_media_group_message(message, context)
        return

    await process_image_messages([message])


async def queue_media_group_message(message, context: CallbackContext):
    context.application.bot_data.setdefault("media_group_lock", asyncio.Lock())
    context.application.bot_data.setdefault("media_groups", {})
    lock = context.application.bot_data["media_group_lock"]
    media_groups = context.application.bot_data["media_groups"]
    key = (message.chat_id, message.media_group_id)

    async with lock:
        media_group = media_groups.setdefault(key, {"messages": [], "task": None})
        media_group["messages"].append(message)
        task = media_group.get("task")
        if task and not task.done():
            task.cancel()
        media_group["task"] = context.application.create_task(
            process_media_group_after_delay(context.application, key)
        )


async def process_media_group_after_delay(application, key):
    try:
        await asyncio.sleep(MEDIA_GROUP_COLLECT_SECONDS)
    except asyncio.CancelledError:
        return

    lock = application.bot_data["media_group_lock"]
    async with lock:
        media_group = application.bot_data["media_groups"].pop(key, None)

    if media_group:
        await process_image_messages(media_group["messages"])


async def process_image_messages(messages):
    message = messages[0]
    if s3_config_missing():
        await reply_text(message, "S3 配置未设置，请联系管理员。")
        return

    if len(messages) == 1:
        await reply_text(messages[0], "正在处理图片，请稍候...")
    else:
        await reply_text(messages[0], f"正在处理 {len(messages)} 张图片，请稍候...")

    results = await asyncio.gather(
        *(download_compress_and_upload(message) for message in messages),
        return_exceptions=True,
    )

    successes = [result for result in results if isinstance(result, tuple)]
    failures = [result for result in results if isinstance(result, Exception)]

    if not successes:
        await reply_text(messages[0], f"上传失败：{failures[0]}")
        return

    if len(successes) == 1:
        object_key, image_url = successes[0]
        reply = f"""\
上传完成：
文件名:
`{object_key}`
链接:
`{image_url}`
markdown:
`![image]({image_url})`"""
    else:
        lines = ["上传完成："]
        for index, (object_key, image_url) in enumerate(successes, start=1):
            lines.extend(
                [
                    f"{index}. `{object_key}`",
                    f"链接: `{image_url}`",
                    f"markdown: `![image]({image_url})`",
                ]
            )
        if failures:
            lines.append(f"失败：{len(failures)} 张")
        reply = "\n".join(lines)

    await reply_text(
        messages[0], reply, disable_web_page_preview=True, parse_mode="Markdown"
    )


async def download_compress_and_upload(message):
    Path("temp").mkdir(exist_ok=True)
    Path("processed").mkdir(exist_ok=True)

    if message.photo:
        photo = message.photo[-1]
        file = await photo.get_file()
        file_path = Path("temp") / f"{message.chat_id}_{message.message_id}.jpg"
        await file.download_to_drive(file_path)

    elif message.document and (
        getattr(message.document, "mime_type", "") or ""
    ).startswith("image/"):
        file = await message.document.get_file()
        ext = Path(message.document.file_name or "").suffix or ".png"
        file_path = Path("temp") / f"{message.chat_id}_{message.message_id}{ext}"
        await file.download_to_drive(file_path)

    else:
        raise ValueError("请发送图片文件。")

    object_key = f"{file.file_id}.webp"
    output_path = Path("processed") / f"{message.chat_id}_{message.message_id}.webp"

    try:
        await asyncio.to_thread(save_lossless_webp, file_path, output_path)
        s3_client = build_s3_client()
        await asyncio.to_thread(
            s3_client.upload_file,
            str(output_path),
            AWS_S3_BUCKET,
            object_key,
            ExtraArgs={"ContentType": "image/webp"},
        )
    except (BotoCoreError, ClientError, OSError):
        logger.exception("Failed to upload image to S3: %s", output_path)
        raise
    finally:
        for path in (file_path, output_path):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    assert AWS_S3_HOST is not None
    assert AWS_S3_BUCKET is not None
    public_base_url = AWS_S3_PUBLIC_URL or f"{AWS_S3_HOST.rstrip('/')}/{AWS_S3_BUCKET}"
    image_url = urljoin(f"{public_base_url.rstrip('/')}/", quote(object_key))
    return object_key, image_url


async def del_image(update: Update, context: CallbackContext):
    message = update.effective_message
    if not message:
        return

    if update.effective_chat and update.effective_chat.id != CHAT_ID:
        await reply_text(message, "你未取得授权使用此机器人，请联系管理员。")
        return

    if not context.args:
        await reply_text(
            message,
            "请添加想要删除的文件名\n例如`/del aabbcc.webp`",
            parse_mode="Markdown",
        )
        return

    if s3_config_missing():
        await reply_text(message, "S3 配置未设置，请联系管理员。")
        return

    filename = Path(context.args[0]).name
    object_key = filename if Path(filename).suffix else f"{filename}.webp"
    s3_client = build_s3_client()

    try:
        await asyncio.to_thread(
            s3_client.delete_object,
            Bucket=AWS_S3_BUCKET,
            Key=object_key,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception("Failed to delete image from S3: %s", object_key)
        await reply_text(message, f"删除失败：{exc}")
        return

    await reply_text(message, f"删除完成：`{object_key}`", parse_mode="Markdown")


def main():
    builder = (
        ApplicationBuilder().token(TG_BOT_TOKEN).concurrent_updates(CONCURRENT_UPDATES)
    )
    if SOCKS_PROXY:
        builder = builder.proxy(SOCKS_PROXY).get_updates_proxy(SOCKS_PROXY)

    application = builder.build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("del", del_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, process_image)
    )
    application.add_error_handler(handle_error)

    application.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
