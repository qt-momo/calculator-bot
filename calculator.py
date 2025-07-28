import logging
import re
import time
import random
import asyncio
import os
from simpleeval import simple_eval, InvalidExpression
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ChatAction, ChatType
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ANSI color codes
GREEN, RED, YELLOW, BLUE, MAGENTA, CYAN, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[94m", "\033[95m", "\033[96m", "\033[0m"
)

class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: CYAN,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: MAGENTA,
    }

    def format(self, record):
        # Add color to levelname and message
        color = self.COLORS.get(record.levelno, RESET)
        record.levelname = f"{color}{record.levelname}{RESET}"
        record.msg = f"{color}{record.getMessage()}{RESET}"
        return super().format(record)

# Logger configuration
handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s"))
logger = logging.getLogger("CalcBot")
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

# Load environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
if BOT_TOKEN:
    logger.info(f"🔑 Loaded BOT_TOKEN from environment.")
else:
    logger.warning("⚠️ BOT_TOKEN environment variable is missing!")
UPDATES_URL = os.getenv('UPDATES_URL')
SUPPORT_URL = os.getenv('SUPPORT_URL')

COMMANDS = [
    BotCommand('start', 'Welcome message'),
    BotCommand('help', 'How to use me'),
]
logger.debug(f"📋 Defined bot commands: {COMMANDS}")

# Updated regex pattern for math expressions including percentage patterns
MATH_PATTERN = re.compile(r'([-+]?\d[\d\.\s]*(?:[+\-*/×÷%]\s*[\d\.\s]+)+|[\d\.]+\s*%\s*of\s*[\d\.]+)')
logger.debug(f"🔧 Compiled MATH_PATTERN: {MATH_PATTERN.pattern}")

# Concurrency control for sending messages
semaphore = asyncio.Semaphore(20)
logger.debug("🔒 Initialized semaphore for message sending with limit 20")

# Extract user and chat information
def extract_user_info(update: Update):
    logger.debug("🔍 extract_user_info called")
    u = update.effective_user
    c = update.effective_chat
    info = {
        "user_id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "chat_id": c.id,
        "chat_type": c.type,
        "chat_title": c.title or c.first_name or "",
        "chat_username": f"@{c.username}" if c.username else "No Username",
        "chat_link": f"https://t.me/{c.username}" if c.username else "No Link",
    }
    logger.info(
        f"ℹ️ User info extracted: {info['full_name']} (@{info['username']}) "
        f"[ID: {info['user_id']}] in {info['chat_title']} [{info['chat_id']}] {info['chat_link']}"
    )
    return info

# Send a message with typing indicator safely
async def safe_send_message(bot, chat_id, text, reply_to=None):
    logger.debug(f"✉️ safe_send_message called with chat_id={chat_id}, reply_to={reply_to}, text='{text}'")
    async with semaphore:
        try:
            logger.debug(f"⌛ Acquired semaphore for sending message to {chat_id}")
            asyncio.create_task(bot.send_chat_action(chat_id, ChatAction.TYPING))
            logger.debug(f"💭 Sent typing action to chat {chat_id}")
            if reply_to:
                asyncio.create_task(bot.send_message(chat_id, text, reply_to_message_id=reply_to))
                logger.debug(f"➡️ Sending reply message to {chat_id}, reply_to {reply_to}")
            else:
                asyncio.create_task(bot.send_message(chat_id, text))
                logger.debug(f"⬆️ Sending new message to {chat_id}")
            logger.info(f"✅ Message queued for chat {chat_id}")
        except Exception as e:
            logger.error(f"❌ Exception in safe_send_message for chat {chat_id}: {e}")

# Handle the /start command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("🚀 start_command invoked")
    if update.effective_chat.type != ChatType.PRIVATE:
        logger.info("ℹ️ start_command ignored: not a private chat")
        return
    ui = extract_user_info(update)
    logger.info(
        f"💬 /start by {ui['full_name']} (@{ui['username']}) "
        f"[ID: {ui['user_id']}] in {ui['chat_title']} [{ui['chat_id']}] {ui['chat_link']}"
    )
    
    # Generate dynamic add me link
    bot_username = context.bot.username
    add_me_link = f"https://t.me/{bot_username}?startgroup=true"
    
    kb = [
        [InlineKeyboardButton("Updates", url=UPDATES_URL), InlineKeyboardButton("Support", url=SUPPORT_URL)],
        [InlineKeyboardButton("Add Me To Your Group", url=add_me_link)],
    ]
    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        logger.debug(f"💭 Typing action sent before /start reply in chat {ui['chat_id']}")
        
        # Create welcome message with user's name
        user_mention = f"<a href='tg://user?id={ui['user_id']}'>{ui['full_name']}</a>"
        welcome_text = (
            f"👋 Hi {user_mention}! I am a Calculator Bot.\n\n"
            "Just send me any math question like 5×5, 20+30, 10% of 100, or 5% of 10 and I will give you the answer."
        )
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='HTML'
        )
        logger.info("✅ Start reply sent successfully")
    except Exception as e:
        logger.error(f"❌ Error sending /start reply: {e}")

# Handle the /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("🚀 help_command invoked")
    ui = extract_user_info(update)
    logger.info(
        f"💬 /help by {ui['full_name']} (@{ui['username']}) "
        f"[ID: {ui['user_id']}] in {ui['chat_title']} [{ui['chat_id']}] {ui['chat_link']}"
    )
    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        logger.debug(f"💭 Typing action sent before /help reply in chat {ui['chat_id']}")
        await update.message.reply_text(
            "💌 How to use me:\n\n"
            "Send me math like:\n"
            "➤ 4+4\n"
            "➤ 8-2\n"
            "➤ 5×5\n"
            "➤ 9÷3\n"
            "➤ 10% of 100\n"
            "➤ 25% of 80\n\n"
            "I will solve it for you!"
        )
        logger.info("✅ Help reply sent successfully")
    except Exception as e:
        logger.error(f"❌ Error sending /help reply: {e}")

# Function to handle percentage calculations
def handle_percentage(expr):
    """Handle percentage calculations like '10% of 100' or '5% of 10'"""
    logger.debug(f"🔢 Handling percentage expression: '{expr}'")
    
    # Handle "X% of Y" format
    percent_of_pattern = re.compile(r'(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)', re.IGNORECASE)
    match = percent_of_pattern.search(expr)
    if match:
        percent_value = float(match.group(1))
        base_value = float(match.group(2))
        result = (percent_value / 100) * base_value
        logger.debug(f"🔢 Calculated {percent_value}% of {base_value} = {result}")
        return result, f"{percent_value}% of {base_value}"
    
    # Handle regular percentage in expressions (like 10%+20 = 10*0.01+20)
    # Only convert standalone % to *0.01, not in "X% of Y" patterns
    if '% of' not in expr.lower():
        safe_expr = expr.replace('%', '*0.01')
        logger.debug(f"🔢 Converted standalone % expression: '{expr}' -> '{safe_expr}'")
        return None, safe_expr  # Return None to indicate normal processing
    
    return None, expr

# Handle callback queries (delete button)
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("🔘 handle_callback_query invoked")
    query = update.callback_query
    await query.answer()
    
    if query.data == "delete_msg":
        try:
            await query.delete_message()
            logger.info("✅ Message deleted via callback")
        except Exception as e:
            logger.error(f"❌ Error deleting message: {e}")

# Calculate math expressions from messages
async def calculate_expression(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("🔢 calculate_expression invoked")
    ui = extract_user_info(update)
    text = update.message.text or ""
    logger.info(
        f"🔍 Received message: '{text}' from {ui['full_name']} (@{ui['username']}) "
        f"[ID: {ui['user_id']}] in {ui['chat_title']} [{ui['chat_id']}] {ui['chat_link']}"
    )
    is_private = update.effective_chat.type == ChatType.PRIVATE
    is_reply_to_bot = (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user.id == context.bot.id
    )
    matches = MATH_PATTERN.findall(text)
    logger.debug(f"🔎 Regex matches found: {matches}")
    if not matches:
        logger.info("ℹ️ No math expression found in message")
        if is_private or is_reply_to_bot:
            try:
                await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
                logger.debug(f"💭 Typing action before reminder in chat {ui['chat_id']}")
                
                # Create delete button
                delete_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑️ Delete", callback_data="delete_msg")]
                ])
                
                await update.message.reply_text(
                    "🤖 I'm a calculator bot. Send me a math expression like 2+2, 3×4, or 10% of 100!",
                    reply_markup=delete_kb
                )
                logger.info("✅ Calculator reminder sent")
            except Exception as e:
                logger.error(f"❌ Error sending calculator reminder: {e}")
        return
    
    for expr in matches:
        start_time = time.time()
        original = expr.strip().replace(" ", "")
        
        # Handle percentage calculations
        percent_result, processed_expr = handle_percentage(expr.strip())
        
        if percent_result is not None:
            # Direct percentage calculation (like "10% of 100")
            result = percent_result
            original_display = processed_expr
            logger.debug(f"🧮 Direct percentage calculation: {original_display} = {result}")
        else:
            # Regular expression processing
            safe = processed_expr.replace("×", "*").replace("÷", "/")
            original_display = original
            logger.debug(f"🧮 Processing expression: original='{original}', safe='{safe}'")
            
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: simple_eval(safe))
                logger.debug(f"🔢 Calculated result: {result}")
            except InvalidExpression:
                logger.error(f"❌ Invalid expression attempted: '{original}' from {ui['full_name']} (@{ui['username']})")
                continue
            except Exception as e:
                logger.error(f"❌ Calculation error for '{original}' from {ui['full_name']} (@{ui['username']}): {e}")
                continue
        
        # Format result
        if isinstance(result, float):
            rounded = round(result, 2)
            logger.debug(f"🔢 Raw result={result}, rounded={rounded}")
            result = rounded
        
        reply = f"{original_display} = {result}"
        await safe_send_message(
            context.bot,
            update.effective_chat.id,
            reply,
            reply_to=update.message.message_id if not is_private else None,
        )
        response_time = round((time.time() - start_time) * 1000)
        logger.debug(f"⏱ Response time: {response_time} ms for expression '{original_display}'")
        if random.random() < 0.1:
            logger.info(
                f"✅ Replied: '{reply}' in {response_time} ms to {ui['full_name']} "
                f"(@{ui['username']}) in {ui['chat_title']} [{ui['chat_id']}] {ui['chat_link']}"
            )

# HTTP health check handler class
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        client = self.client_address
        logger.info(f"🌐 Health check GET received from {client}")
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Telegram bot is running and healthy!")
            logger.info("✅ Health check response sent")
        except Exception as e:
            logger.error(f"❌ Error handling health check GET: {e}")

    def do_HEAD(self):
        client = self.client_address
        logger.info(f"🌐 Health check HEAD received from {client}")
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            logger.info("✅ Health check HEAD response sent")
        except Exception as e:
            logger.error(f"❌ Error handling health check HEAD: {e}")

    def log_message(self, format, *args):
        # Override default logging to suppress console spam; handled by logger
        pass

# Start HTTP health check server
def start_dummy_server():
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"🌐 Starting HTTP server on port {port}")
    try:
        server = HTTPServer(("0.0.0.0", port), DummyHandler)
        logger.info(f"✅ HTTP server listening on port {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ HTTP server error: {e}")

# Initialize and run the bot
def main():
    logger.info("🚀 Bot is starting...")
    if not BOT_TOKEN:
        logger.critical("🔒 Cannot start bot: BOT_TOKEN not set. Exiting.")
        return
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        logger.debug("🔧 Telegram Application built successfully")
        
        app.add_handler(CommandHandler('start', start_command))
        logger.debug("➕ Added /start handler")
        
        app.add_handler(CommandHandler('help', help_command))
        logger.debug("➕ Added /help handler")
        
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, calculate_expression))
        logger.debug("➕ Added message handler for calculations")
        
        app.add_handler(CallbackQueryHandler(handle_callback_query))
        logger.debug("➕ Added callback query handler for delete button")

        async def set_commands(app):
            logger.debug("🔧 Setting bot commands via API")
            await app.bot.set_my_commands(COMMANDS)
            logger.info("✅ Bot commands registered successfully 🎉  ➤ /start - Welcome  ➤ /help - How to use me")

        app.post_init = set_commands
        logger.debug("🔧 post_init hook set for setting commands")

        # Start polling (blocking call)
        app.run_polling()
        logger.info("📡 Bot polling started")
        
    except Exception as e:
        logger.critical(f"❌ Fatal error in main: {e}")

if __name__ == '__main__':
    logger.debug("🧵 Starting health check server thread")
    threading.Thread(target=start_dummy_server, daemon=True).start()
    logger.debug("🧵 Health check server thread started")
    main()