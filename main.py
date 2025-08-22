# main.py

import logging
from datetime import datetime, time as dtime
from pytz import utc
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- CONFIGURATION ---

AUTHORIZED_USERS = [5818833182]  # Replace with your Telegram user ID(s)
CHANNEL_ID = -1002767091522     # Replace with your Telegram channel ID (negative for channels)

MONGODB_URI = "mongodb+srv://rfbotuser:rfbotuser@cluster0.xh0wm3h.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot"
COLLECTION_NAME = "scheduled_posts"

# States for ConversationHandler
(
    ADD_WAITING_FOR_MESSAGE,
    ADD_WAITING_FOR_TIME,
) = range(2)

EDIT_SELECT_POST, EDIT_WAITING_FOR_MESSAGE, EDIT_WAITING_FOR_TIME = range(2, 5)

# --- SETUP ---

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

scheduler = AsyncIOScheduler(timezone=utc)
scheduler.start()

# --- HELPERS ---

def is_authorized(user_id: int) -> bool:
    return user_id in AUTHORIZED_USERS

def build_main_menu():
    keyboard = [
        [InlineKeyboardButton("Add Scheduled Post", callback_data="add")],
        [InlineKeyboardButton("List Scheduled Posts", callback_data="list")],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_post_buttons(post_id):
    keyboard = [
        [
            InlineKeyboardButton("Edit", callback_data=f"edit|{post_id}"),
            InlineKeyboardButton("Delete", callback_data=f"delete|{post_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def post_to_channel(post):
    try:
        bot = application.bot
        channel_id = post["channel_id"]
        msg_type = post["message_type"]
        if msg_type == "text":
            bot.send_message(chat_id=channel_id, text=post["content"])
        elif msg_type == "image":
            bot.send_photo(chat_id=channel_id, photo=post["image_file_id"], caption=post.get("content", ""))
        else:
            logger.warning("Unknown message_type in scheduled post: %s", msg_type)
    except Exception as e:
        logger.error(f"Error posting message to channel: {e}")

def schedule_existing_posts():
    # Load all scheduled posts from DB and schedule them
    posts = list(collection.find())
    for post in posts:
        post_time = post["schedule_time"]
        hour, minute = map(int, post_time.split(':'))
        job_id = str(post["_id"])
        scheduler.add_job(
            post_to_channel,
            "cron",
            hour=hour,
            minute=minute,
            args=[post],
            id=job_id,
            replace_existing=True,
            timezone=utc
        )
    logger.info(f"Scheduled {len(posts)} posts.")

def parse_time_str(time_str):
    try:
        hour, minute = map(int, time_str.split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return dtime(hour=hour, minute=minute)
    except Exception:
        return None
    return None

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return
    await update.message.reply_text(
        "Welcome to the Daily Post Scheduler Bot.\nSelect an action:", 
        reply_markup=build_main_menu()
    )

# CallbackQuery handler for main menu buttons and actions

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_authorized(user_id):
        await query.edit_message_text("❌ You are not authorized.")
        return

    data = query.data

    if data == "add":
        await query.edit_message_text("Send me the message or photo you want to schedule daily.")
        return ADD_WAITING_FOR_MESSAGE

    elif data == "list":
        posts = list(collection.find({"user_id": user_id}))
        if not posts:
            await query.edit_message_text("No scheduled posts found.", reply_markup=build_main_menu())
            return
        text_msgs = []
        for post in posts:
            preview = post["content"] if post["message_type"] == "text" else "[Image]"
            text_msgs.append(f"ID: {post['_id']}\nTime: {post['schedule_time']}\n{preview}\n")
        msg_text = "\n---\n".join(text_msgs)
        await query.edit_message_text(msg_text, reply_markup=build_main_menu())
        return

    elif data.startswith("delete|"):
        post_id = data.split("|")[1]
        collection.delete_one({"_id": post_id, "user_id": user_id})
        # Remove scheduled job
        try:
            scheduler.remove_job(post_id)
        except Exception:
            pass
        await query.edit_message_text(f"Deleted scheduled post {post_id}.", reply_markup=build_main_menu())
        return

    # Additional Edit handler (for simplicity, edit not implemented fully here)
    elif data.startswith("edit|"):
        await query.edit_message_text("Edit feature coming soon!", reply_markup=build_main_menu())
        return

    else:
        await query.edit_message_text("Unknown action.", reply_markup=build_main_menu())
        return


# Add Post conversation: receive message or photo

async def add_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized.")
        return ConversationHandler.END

    if update.message.photo:
        # Take largest photo size
        photo = update.message.photo[-1]
        context.user_data["message_type"] = "image"
        context.user_data["image_file_id"] = photo.file_id
        context.user_data["content"] = update.message.caption or ""
    elif update.message.text:
        context.user_data["message_type"] = "text"
        context.user_data["content"] = update.message.text
        context.user_data["image_file_id"] = None
    else:
        await update.message.reply_text("Please send either a text message or a photo with optional caption to schedule.")
        return ADD_WAITING_FOR_MESSAGE

    await update.message.reply_text("Send me the daily posting time in 24h format (HH:MM), e.g., 09:30")
    return ADD_WAITING_FOR_TIME

async def add_receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized.")
        return ConversationHandler.END

    time_str = update.message.text.strip()
    valid_time = parse_time_str(time_str)
    if not valid_time:
        await update.message.reply_text("Invalid time format. Please send time as HH:MM in 24h format, e.g. 14:45")
        return ADD_WAITING_FOR_TIME

    # Save to DB
    message_type = context.user_data["message_type"]
    content = context.user_data["content"]
    image_file_id = context.user_data["image_file_id"]

    post_doc = {
        "user_id": user_id,
        "channel_id": CHANNEL_ID,
        "message_type": message_type,
        "content": content,
        "image_file_id": image_file_id,
        "schedule_time": time_str,
    }
    res = collection.insert_one(post_doc)
    post_id = str(res.inserted_id)

    # Schedule the job
    hour, minute = valid_time.hour, valid_time.minute

    # Because job stores post copy, we re-load it fresh each time from DB to avoid stale data, so define wrapper:
    def job_callback():
        fresh_post = collection.find_one({"_id": res.inserted_id})
        if fresh_post:
            post_to_channel(fresh_post)

    scheduler.add_job(
        job_callback,
        trigger="cron",
        hour=hour,
        minute=minute,
        id=post_id,
        replace_existing=True,
        timezone=utc
    )

    await update.message.reply_text(f"Scheduled your post daily at {time_str} UTC.", reply_markup=build_main_menu())
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation canceled.", reply_markup=build_main_menu())
    return ConversationHandler.END

# --- MAIN ---

if __name__ == "__main__":
    import os
    from telegram.ext import filters

    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        print("Error: Set your TELEGRAM_BOT_TOKEN environment variable.")
        exit(1)

    application = ApplicationBuilder().token(TOKEN).build()

    # Conversation handler for adding posts
    add_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="add")],
        states={
            ADD_WAITING_FOR_MESSAGE: [MessageHandler(filters.TEXT | filters.PHOTO, add_receive_message)],
            ADD_WAITING_FOR_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(add_conv_handler)

    # Schedule all existing posts on startup
    schedule_existing_posts()

    application.run_polling()
