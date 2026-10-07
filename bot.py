"""Telegram-бот для постов с товарами.

Что умеет:
  1. Принимает фото товара → убирает фон → ставит товар по центру на твой фон.
  2. Показывает готовый пост и кнопки с темами группы → публикует в выбранную тему.

Команды в личке с ботом:
  /start            — приветствие и подсказка
  /темы  (/topics)  — список запомненных тем (там же можно удалить лишнюю)
  /фон   (/fon)     — заменить фон: после команды пришли новую картинку
  /отмена (/cancel) — отменить ожидание

Команда в группе (пишешь внутри нужной темы):
  /тема  (/topic)            — запомнить эту тему под её названием
  /тема Своё название        — запомнить под своим названием
"""

import asyncio
import json
import logging
import os
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv, set_key
from PIL import Image
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from image_processing import make_product_photo

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = BASE_DIR / "templates"
BACKGROUND = TEMPLATES / "background.jpg"
TOPICS_FILE = BASE_DIR / "topics.json"

load_dotenv(BASE_DIR / ".env")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

logging.basicConfig(format="%(asctime)s  %(levelname)s  %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

HELP = (
    "Пришли мне фото товара, а описание напиши в подписи к фото.\n"
    "Я уберу фон, поставлю товар на твой фон и спрошу, в какую тему опубликовать.\n\n"
    "Как научить меня темам группы:\n"
    "1) добавь меня в группу администратором;\n"
    "2) зайди в каждую тему и напиши там /тема — я запомню её.\n\n"
    "Команды:\n"
    "/темы — какие темы я знаю\n"
    "/фон — заменить фон\n"
    "/отмена — отменить"
)

PRIVATE = filters.ChatType.PRIVATE
GROUPS = filters.ChatType.GROUPS


def _cmd(*names: str, with_text: bool = False) -> filters.BaseFilter:
    """Команда, которую можно писать и по-русски (/фон), и латиницей (/fon).
    Telegram не считает русские слова командами, поэтому ловим их как обычный текст."""
    tail = r"(\s+.*)?" if with_text else r"\s*"
    return filters.Regex(rf"(?s)^/({'|'.join(names)})(@\w+)?{tail}$")


# ---------- Темы: хранятся в файле topics.json ----------

def load_topics() -> dict:
    if TOPICS_FILE.exists():
        return json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    return {}


def save_topics(topics: dict) -> None:
    TOPICS_FILE.write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")


def topic_key(chat_id: int, thread_id: int | None) -> str:
    return f"{chat_id}:{thread_id or 0}"


# ---------- Владелец ----------

def is_admin_id(user_id: int) -> bool:
    return bool(ADMIN_ID) and str(user_id) == ADMIN_ID


async def is_owner(update: Update) -> bool:
    """Бот слушается только владельца (ADMIN_ID из .env).
    Если владелец ещё не записан — им становится первый, кто напишет боту в личку."""
    global ADMIN_ID
    user_id = str(update.effective_user.id)
    if not ADMIN_ID:
        ADMIN_ID = user_id
        set_key(str(BASE_DIR / ".env"), "ADMIN_ID", user_id, quote_mode="never")
        log.info("Владелец бота записан: %s", user_id)
        await update.effective_message.reply_text("Я тебя запомнил — теперь слушаюсь только тебя 👍")
    if user_id != ADMIN_ID:
        await update.effective_message.reply_text("Извини, это личный бот.")
        return False
    return True


# ---------- Команды в личке ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await is_owner(update):
        await update.message.reply_text(HELP)


async def ask_background(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_owner(update):
        return
    context.user_data["waiting_for"] = "background"
    await update.message.reply_text("Пришли новую картинку для фона.\n/отмена — передумал")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_owner(update):
        return
    context.user_data.pop("waiting_for", None)
    await update.message.reply_text("Ок, отменил.")


def topics_list_markup(topics: dict) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"❌ Удалить «{t['name']}»", callback_data=f"del|{key}")]
            for key, t in topics.items()]
    return InlineKeyboardMarkup(rows)


async def list_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_owner(update):
        return
    topics = load_topics()
    if not topics:
        await update.message.reply_text(
            "Я пока не знаю ни одной темы.\n\n"
            "Добавь меня в группу администратором, зайди в нужную тему и напиши там /тема"
        )
        return
    lines = [f"• {t['name']}  (группа «{t['chat_title']}»)" for t in topics.values()]
    await update.message.reply_text("Темы, которые я знаю:\n" + "\n".join(lines),
                                    reply_markup=topics_list_markup(topics))


# ---------- Команда в группе: запомнить тему ----------

async def register_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_admin_id(update.effective_user.id):
        return  # чужие команды в группе молча игнорируем

    thread_id = msg.message_thread_id if msg.is_topic_message else None

    # Название: то, что написано после команды, иначе — настоящее название темы
    parts = msg.text.split(maxsplit=1)
    name = parts[1].strip() if len(parts) > 1 else ""
    if not name and msg.reply_to_message and msg.reply_to_message.forum_topic_created:
        name = msg.reply_to_message.forum_topic_created.name
    if not name:
        name = "Основной чат" if thread_id is None else f"Тема {thread_id}"

    topics = load_topics()
    topics[topic_key(msg.chat_id, thread_id)] = {
        "name": name, "chat_id": msg.chat_id, "thread_id": thread_id,
        "chat_title": msg.chat.title or "",
    }
    save_topics(topics)
    log.info("Запомнил тему «%s» (%s, тема %s)", name, msg.chat_id, thread_id)

    try:
        await msg.delete()  # убираем команду, чтобы не мусорить в группе
    except Exception:
        pass
    await context.bot.send_message(
        int(ADMIN_ID),
        f"✅ Запомнил тему «{name}» в группе «{msg.chat.title}».\n"
        "Чтобы назвать её иначе, напиши в этой теме: /тема Новое название",
    )


async def topic_changed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Если в знакомой группе создали или переименовали тему — запоминаем сами."""
    msg = update.message
    topics = load_topics()
    if not any(t["chat_id"] == msg.chat_id for t in topics.values()):
        return  # группа не наша
    if msg.forum_topic_created:
        name = msg.forum_topic_created.name
    elif msg.forum_topic_edited and msg.forum_topic_edited.name:
        name = msg.forum_topic_edited.name
    else:
        return
    topics[topic_key(msg.chat_id, msg.message_thread_id)] = {
        "name": name, "chat_id": msg.chat_id, "thread_id": msg.message_thread_id,
        "chat_title": msg.chat.title or "",
    }
    save_topics(topics)


# ---------- Фото товара → готовый пост ----------

async def download_image(update: Update) -> bytes:
    msg = update.message
    file = await (msg.document.get_file() if msg.document else msg.photo[-1].get_file())
    return bytes(await file.download_as_bytearray())


async def save_background(update: Update, data: bytes) -> None:
    img = Image.open(BytesIO(data)).convert("RGB")
    img.thumbnail((2000, 2000), Image.LANCZOS)  # слишком большие уменьшаем
    img.save(BACKGROUND, "JPEG", quality=95)
    await update.message.reply_text(f"Готово! Новый фон сохранён ({img.width}×{img.height}).")


def post_markup(post_id: str) -> InlineKeyboardMarkup:
    """Кнопки под готовым постом: по одной на каждую тему + изменить текст + отмена."""
    rows = [[InlineKeyboardButton(f"📤 {t['name']}", callback_data=f"pub|{post_id}|{key}")]
            for key, t in load_topics().items()]
    rows.append([InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit|{post_id}"),
                 InlineKeyboardButton("❌ Отмена", callback_data=f"cancel|{post_id}")])
    return InlineKeyboardMarkup(rows)


async def on_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_owner(update):
        return

    data = await download_image(update)
    if context.user_data.get("waiting_for") == "background":
        await save_background(update, data)
        context.user_data.pop("waiting_for", None)
        return

    if not BACKGROUND.exists():
        await update.message.reply_text("Сначала пришли мне фон: команда /фон")
        return
    status = await update.message.reply_text("Обрабатываю фото… (первый раз может занять минуту)")
    try:
        # Тяжёлую работу делаем в отдельном потоке, чтобы бот не «зависал»
        result = await asyncio.to_thread(make_product_photo, data, BACKGROUND)
    except Exception:
        log.exception("Ошибка обработки фото")
        await status.edit_text("Не получилось обработать фото 😕 Попробуй другое.")
        return

    caption = (update.message.caption or "")[:1024]
    context.user_data["post_counter"] = context.user_data.get("post_counter", 0) + 1
    post_id = str(context.user_data["post_counter"])

    preview = await update.message.reply_photo(result, caption=caption or None,
                                               reply_markup=post_markup(post_id))
    context.user_data.setdefault("posts", {})[post_id] = {
        "file_id": preview.photo[-1].file_id,
        "caption": caption,
    }
    await status.delete()

    if not load_topics():
        await update.message.reply_text(
            "Я пока не знаю тем группы. Добавь меня в группу админом, "
            "зайди в нужную тему и напиши там /тема — потом кнопки появятся."
        )
    elif not caption:
        await update.message.reply_text("Описания нет. Нажми «✏️ Изменить текст», чтобы добавить.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_owner(update):
        return

    waiting = context.user_data.get("waiting_for", "")
    if waiting.startswith("text|"):
        post_id = waiting.split("|")[1]
        post = context.user_data.get("posts", {}).get(post_id)
        context.user_data.pop("waiting_for", None)
        if not post:
            await update.message.reply_text("Этот пост уже не найден. Пришли фото заново.")
            return
        post["caption"] = update.message.text[:1024]
        # Присылаем обновлённый пост заново, с теми же кнопками
        await update.message.reply_photo(post["file_id"], caption=post["caption"],
                                         reply_markup=post_markup(post_id))
        return

    await update.message.reply_text("Пришли фото товара 📷\n\n" + HELP)


# ---------- Нажатия на кнопки ----------

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin_id(query.from_user.id):
        await query.answer("Это личный бот.")
        return

    action, *args = query.data.split("|")
    posts = context.user_data.setdefault("posts", {})

    if action == "del":
        topics = load_topics()
        removed = topics.pop(args[0], None)
        save_topics(topics)
        await query.answer(f"Удалил «{removed['name']}»" if removed else "Уже удалено")
        await query.edit_message_reply_markup(topics_list_markup(topics))
        return

    post_id = args[0]
    post = posts.get(post_id)
    if not post:
        await query.answer("Этот пост уже не найден. Пришли фото заново.", show_alert=True)
        return

    if action == "pub":
        topic = load_topics().get(args[1])
        if not topic:
            await query.answer("Такой темы больше нет.", show_alert=True)
            return
        try:
            await context.bot.send_photo(topic["chat_id"], post["file_id"],
                                         caption=post["caption"] or None,
                                         message_thread_id=topic["thread_id"])
        except Exception as e:
            log.exception("Не получилось опубликовать")
            await query.answer(f"Не получилось опубликовать: {e}", show_alert=True)
            return
        posts.pop(post_id, None)
        await query.answer("Опубликовано!")
        await query.edit_message_reply_markup(None)
        await query.message.reply_text(f"✅ Опубликовано в тему «{topic['name']}»")

    elif action == "edit":
        context.user_data["waiting_for"] = f"text|{post_id}"
        await query.answer()
        await query.message.reply_text("Пришли новый текст описания одним сообщением.")

    elif action == "cancel":
        posts.pop(post_id, None)
        await query.answer("Отменено")
        await query.edit_message_reply_markup(None)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Не найден BOT_TOKEN. Создай файл .env (по образцу .env.example) и впиши токен.")
    TEMPLATES.mkdir(exist_ok=True)

    app = Application.builder().token(BOT_TOKEN).build()

    # Личка с ботом
    app.add_handler(CommandHandler(["start", "help"], start, filters=PRIVATE))
    app.add_handler(CommandHandler("fon", ask_background, filters=PRIVATE))
    app.add_handler(CommandHandler("topics", list_topics, filters=PRIVATE))
    app.add_handler(CommandHandler("cancel", cancel, filters=PRIVATE))
    app.add_handler(MessageHandler(PRIVATE & _cmd("фон"), ask_background))
    app.add_handler(MessageHandler(PRIVATE & _cmd("темы"), list_topics))
    app.add_handler(MessageHandler(PRIVATE & _cmd("отмена"), cancel))
    app.add_handler(MessageHandler(PRIVATE & (filters.PHOTO | filters.Document.IMAGE), on_image))
    app.add_handler(MessageHandler(PRIVATE & filters.TEXT, on_text))

    # Группа
    app.add_handler(MessageHandler(GROUPS & _cmd("тема", "topic", with_text=True), register_topic))
    app.add_handler(MessageHandler(
        GROUPS & (filters.StatusUpdate.FORUM_TOPIC_CREATED | filters.StatusUpdate.FORUM_TOPIC_EDITED),
        topic_changed))

    # Кнопки
    app.add_handler(CallbackQueryHandler(on_button))

    log.info("Бот запущен. Останови его: Ctrl+C")
    app.run_polling()


if __name__ == "__main__":
    main()
