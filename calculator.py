import os
import re
import sys
import time
import asyncio
import random
import logging
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

from simpleeval import simple_eval, InvalidExpression

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ChatAction, ChatType
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
UPDATES_URL = os.getenv('UPDATES_URL')
SUPPORT_URL = os.getenv('SUPPORT_URL')
PORT = int(os.environ.get("PORT", 8000))

# Bot commands for menu
COMMANDS = [
    BotCommand('start', 'Welcome message'),
    BotCommand('help', 'How to use me'),
]

# Message templates
MESSAGES = {
    'start': "👋 Hi <a href='tg://user?id={user_id}'>{full_name}</a>! I am a Calculator Bot.\n\n"
             "Just send me any math question like 5×5 or 20+30 and I will give you the answer.",
    'help': "💌 How to use me, <a href='tg://user?id={user_id}'>{full_name}</a>:\n\n"
            "Send me math like:\n"
            "➤ 4+4\n"
            "➤ 8-2\n"
            "➤ 5×5\n"
            "➤ 9÷3\n\n"
            "I will solve it for you!",
    'calculator_reminder': "🤖 <a href='tg://user?id={user_id}'>{full_name}</a>, I'm a calculator bot. Send me a math expression like 2+2 or 3×4!",
    'division_error': "❌ Error: Division by zero in '{expression}'",
    'generic_error': "❌ Sorry, there was an error processing your request.",
    'help_error': "❌ Sorry, there was an error processing your help request.",
    'unexpected_error': "❌ Sorry, there was an unexpected error processing your message.",
    'error_occurred': "❌ Sorry, I encountered an error while processing your request. Please try again later.",
    'pinging': "🛰️ Pinging...",
    'pong': "🏓 <a href='https://t.me/SoulMeetsHQ'>Pong!</a> {ms}ms",
    'health_check': "Telegram bot is running and healthy!"
}

# ANSI color codes for logging
GREEN, RED, YELLOW, BLUE, MAGENTA, CYAN, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[94m", "\033[95m", "\033[96m", "\033[0m"
)

# Math pattern for expressions
MATH_PATTERN = re.compile(r'\b(\d+(?:\.\d+)?\s*[+\-*/×÷]\s*\d+(?:\.\d+)?(?:\s*[+\-*/×÷]\s*\d+(?:\.\d+)?)*)\b')

# Concurrency control
semaphore = asyncio.Semaphore(20)

class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: CYAN,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: MAGENTA,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, RESET)
        record.levelname = f"{color}{record.levelname}{RESET}"
        record.msg = f"{color}{record.getMessage()}{RESET}"
        return super().format(record)

# Logger setup
handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter("%(asctime)s - %(levelname)s - %(name)s - %(funcName)s:%(lineno)d - %(message)s"))
logger = logging.getLogger("CalcBot")
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

# File logging
file_handler = logging.FileHandler('bot.log', encoding='utf-8')
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(funcName)s:%(lineno)d - %(message)s"))
file_handler.setLevel(logging.INFO)
logger.addHandler(file_handler)

if BOT_TOKEN:
    logger.info(f"🔑 Loaded BOT_TOKEN from environment.")
else:
    logger.warning("⚠️ BOT_TOKEN environment variable is missing!")

logger.debug(f"📋 Defined bot commands: {COMMANDS}")
logger.debug(f"🔧 Compiled MATH_PATTERN: {MATH_PATTERN.pattern}")
logger.debug("🔒 Initialized semaphore for message sending with limit 20")

def is_valid_math_expression(expr):
    """Check if expression is calculable"""
    try:
        clean_expr = expr.strip().replace(" ", "").replace("×", "*").replace("÷", "/")
        
        # Check incomplete expressions
        if clean_expr.endswith(('+', '-', '*', '/', '×', '÷')):
            return False
            
        if clean_expr.startswith(('*', '/', '×', '÷')):
            return False
            
        # Must contain operator and operands
        operators = ['+', '-', '*', '/', '×', '÷']
        has_operator = any(op in clean_expr for op in operators)
        if not has_operator:
            return False
            
        # Try evaluation
        safe_expr = clean_expr.replace("×", "*").replace("÷", "/")
        simple_eval(safe_expr)
        return True
        
    except Exception:
        return False

def extract_user_info(update: Update):
    """Extract user and chat info"""
    logger.debug("🔍 extract_user_info called")
    try:
        u = update.effective_user
        c = update.effective_chat
        info = {
            "user_id": u.id if u else None,
            "username": u.username if u else None,
            "full_name": u.full_name if u else "Unknown User",
            "chat_id": c.id if c else None,
            "chat_type": c.type if c else None,
            "chat_title": c.title or c.first_name or "" if c else "",
            "chat_username": f"@{c.username}" if c and c.username else "No Username",
            "chat_link": f"https://t.me/{c.username}" if c and c.username else "No Link",
        }
        logger.info(
            f"ℹ️ User info extracted: {info['full_name']} (@{info['username']}) "
            f"[ID: {info['user_id']}] in {info['chat_title']} [{info['chat_id']}] {info['chat_link']}"
        )
        return info
    except Exception as e:
        logger.error(f"❌ Error extracting user info: {e}")
        logger.error(f"🔍 Traceback: {traceback.format_exc()}")
        return {
            "user_id": None,
            "username": None,
            "full_name": "Unknown User",
            "chat_id": None,
            "chat_type": None,
            "chat_title": "",
            "chat_username": "No Username",
            "chat_link": "No Link",
        }

async def safe_send_message(bot, chat_id, text, reply_to=None, reply_markup=None):
    """Send message with typing indicator"""
    logger.debug(f"✉️ safe_send_message called with chat_id={chat_id}, reply_to={reply_to}, text='{text[:50]}...'")
    async with semaphore:
        try:
            logger.debug(f"⌛ Acquired semaphore for sending message to {chat_id}")
            
            # Send typing action
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
                logger.debug(f"💭 Sent typing action to chat {chat_id}")
            except Exception as typing_error:
                logger.warning(f"⚠️ Failed to send typing action to {chat_id}: {typing_error}")
            
            # Send message
            if reply_to:
                message = await bot.send_message(chat_id, text, reply_to_message_id=reply_to, reply_markup=reply_markup, parse_mode='HTML', disable_web_page_preview=True)
                logger.debug(f"➡️ Sent reply message to {chat_id}, reply_to {reply_to}, message_id={message.message_id}")
            else:
                message = await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode='HTML', disable_web_page_preview=True)
                logger.debug(f"⬆️ Sent new message to {chat_id}, message_id={message.message_id}")
            
            logger.info(f"✅ Message sent successfully to chat {chat_id}")
            return message
            
        except Exception as e:
            logger.error(f"❌ Exception in safe_send_message for chat {chat_id}: {e}")
            logger.error(f"🔍 Traceback: {traceback.format_exc()}")
            return None

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle remove button clicks"""
    logger.debug("🔘 handle_callback_query invoked")
    query = update.callback_query
    
    try:
        if query.data == "remove_message":
            ui = extract_user_info(update)
            logger.info(f"🗑️ Remove button clicked by {ui['full_name']} (@{ui['username']}) [ID: {ui['user_id']}] in chat {ui['chat_id']}")
            
            try:
                await query.answer("Message removed! 🗑️", show_alert=False)
                logger.debug("✅ Popup notification sent for message removal")
                
                await query.delete_message()
                logger.info(f"✅ Message {query.message.message_id} deleted successfully from chat {ui['chat_id']}")
                
            except Exception as delete_error:
                logger.error(f"❌ Error deleting message {query.message.message_id}: {delete_error}")
                logger.error(f"🔍 Delete error traceback: {traceback.format_exc()}")
                try:
                    await query.answer("Failed to remove message ❌", show_alert=False)
                except Exception as answer_error:
                    logger.error(f"❌ Failed to send error popup: {answer_error}")
        else:
            logger.warning(f"⚠️ Unknown callback data received: {query.data}")
            await query.answer("Unknown action")
            
    except Exception as e:
        logger.error(f"❌ Error in handle_callback_query: {e}")
        logger.error(f"🔍 Callback query traceback: {traceback.format_exc()}")
        try:
            await query.answer("An error occurred", show_alert=True)
        except Exception as answer_error:
            logger.error(f"❌ Failed to send error response: {answer_error}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    logger.debug("🚀 start_command invoked")
    
    try:
        if update.effective_chat.type != ChatType.PRIVATE:
            logger.info("ℹ️ start_command ignored: not a private chat")
            return
            
        ui = extract_user_info(update)
        logger.info(
            f"💬 /start by {ui['full_name']} (@{ui['username']}) "
            f"[ID: {ui['user_id']}] in {ui['chat_title']} [{ui['chat_id']}] {ui['chat_link']}"
        )
        
        # Generate bot adding link
        try:
            bot_info = await context.bot.get_me()
            add_me_link = f"https://t.me/{bot_info.username}?startgroup=true"
            logger.debug(f"🔗 Generated add me link: {add_me_link}")
        except Exception as bot_info_error:
            logger.error(f"❌ Failed to get bot info: {bot_info_error}")
            logger.error(f"🔍 Bot info traceback: {traceback.format_exc()}")
            add_me_link = "https://t.me/YourBotUsername?startgroup=true"
        
        kb = [
            [InlineKeyboardButton("Updates", url=UPDATES_URL), InlineKeyboardButton("Support", url=SUPPORT_URL)],
            [InlineKeyboardButton("Add Me To Your Group", url=add_me_link)],
        ]
        
        try:
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            logger.debug(f"💭 Typing action sent before /start reply in chat {ui['chat_id']}")
        except Exception as typing_error:
            logger.warning(f"⚠️ Failed to send typing action: {typing_error}")
        
        message = await update.message.reply_text(
            MESSAGES['start'].format(**ui),
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='HTML'
        )
        logger.info(f"✅ Start reply sent successfully, message_id: {message.message_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in start_command: {e}")
        logger.error(f"🔍 Start command traceback: {traceback.format_exc()}")
        try:
            await update.message.reply_text(MESSAGES['generic_error'])
        except Exception as fallback_error:
            logger.error(f"❌ Failed to send fallback error message: {fallback_error}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    logger.debug("🚀 help_command invoked")
    
    try:
        ui = extract_user_info(update)
        logger.info(
            f"💬 /help by {ui['full_name']} (@{ui['username']}) "
            f"[ID: {ui['user_id']}] in {ui['chat_title']} [{ui['chat_id']}] {ui['chat_link']}"
        )
        
        try:
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            logger.debug(f"💭 Typing action sent before /help reply in chat {ui['chat_id']}")
        except Exception as typing_error:
            logger.warning(f"⚠️ Failed to send typing action: {typing_error}")
        
        message = await update.message.reply_text(
            MESSAGES['help'].format(**ui),
            parse_mode='HTML'
        )
        logger.info(f"✅ Help reply sent successfully, message_id: {message.message_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in help_command: {e}")
        logger.error(f"🔍 Help command traceback: {traceback.format_exc()}")
        try:
            await update.message.reply_text(MESSAGES['help_error'])
        except Exception as fallback_error:
            logger.error(f"❌ Failed to send fallback error message: {fallback_error}")

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ping command with edit"""
    logger.debug("🏓 ping_command invoked")
    
    try:
        ui = extract_user_info(update)
        logger.info(
            f"💬 /ping by {ui['full_name']} (@{ui['username']}) "
            f"[ID: {ui['user_id']}] in {ui['chat_title']} [{ui['chat_id']}] {ui['chat_link']}"
        )
        
        start_time = time.time()
        
        # Send initial pinging message
        message = await update.message.reply_text(MESSAGES['pinging'])
        logger.debug(f"🛰️ Initial ping message sent, message_id: {message.message_id}")
        
        # Calculate response time
        end_time = time.time()
        response_time = round((end_time - start_time) * 1000, 2)
        
        # Edit message to show pong with time
        await message.edit_text(
            MESSAGES['pong'].format(ms=response_time),
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        logger.info(f"🏓 Ping reply edited successfully: {response_time}ms")
        
    except Exception as e:
        logger.error(f"❌ Error in ping_command: {e}")
        logger.error(f"🔍 Ping command traceback: {traceback.format_exc()}")
        try:
            await update.message.reply_text(MESSAGES['generic_error'])
        except Exception as fallback_error:
            logger.error(f"❌ Failed to send fallback error message: {fallback_error}")

async def calculate_expression(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calculate math expressions from messages"""
    logger.debug("🔢 calculate_expression invoked")
    
    try:
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
        
        # Filter valid expressions
        valid_matches = []
        for match in matches:
            if is_valid_math_expression(match):
                valid_matches.append(match)
                logger.debug(f"✅ Valid math expression: {match}")
            else:
                logger.debug(f"❌ Invalid/incomplete math expression ignored: {match}")
        
        if not valid_matches:
            logger.info("ℹ️ No valid math expression found in message")
            if is_private or is_reply_to_bot:
                try:
                    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
                    logger.debug(f"💭 Typing action before reminder in chat {ui['chat_id']}")
                except Exception as typing_error:
                    logger.warning(f"⚠️ Failed to send typing action: {typing_error}")
                
                # Create remove button
                remove_kb = [[InlineKeyboardButton("🗑️ Remove", callback_data="remove_message")]]
                remove_markup = InlineKeyboardMarkup(remove_kb)
                
                try:
                    message = await update.message.reply_text(
                        MESSAGES['calculator_reminder'].format(**ui),
                        reply_markup=remove_markup,
                        parse_mode='HTML'
                    )
                    logger.info(f"✅ Calculator reminder sent, message_id: {message.message_id}")
                except Exception as reminder_error:
                    logger.error(f"❌ Error sending calculator reminder: {reminder_error}")
                    logger.error(f"🔍 Reminder error traceback: {traceback.format_exc()}")
            return
        
        for expr in valid_matches:
            start_time = time.time()
            original = expr.strip().replace(" ", "")
            safe = original.replace("×", "*").replace("÷", "/")
            logger.debug(f"🧮 Processing expression: original='{original}', safe='{safe}'")
            
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: simple_eval(safe))
                
                if isinstance(result, float):
                    # Handle floating point precision
                    if result.is_integer():
                        result = int(result)
                    else:
                        rounded = round(result, 8)
                        if abs(rounded - round(rounded)) < 1e-10:
                            result = round(rounded)
                        else:
                            result = round(rounded, 6)
                    logger.debug(f"🔢 Processed result: {result}")
                
                reply = f"{original} = {result}"
                
                try:
                    # Create delete button for calculation results
                    delete_kb = [[InlineKeyboardButton("🗑️", callback_data="remove_message")]]
                    delete_markup = InlineKeyboardMarkup(delete_kb)
                    
                    message = await safe_send_message(
                        context.bot,
                        update.effective_chat.id,
                        reply,
                        reply_to=update.message.message_id if not is_private else None,
                        reply_markup=delete_markup,
                    )
                    
                    response_time = round((time.time() - start_time) * 1000)
                    logger.debug(f"⏱ Response time: {response_time} ms for expression '{original}'")
                    
                    if message:
                        if random.random() < 0.1:  # Log 10% calculations
                            logger.info(
                                f"✅ Replied: '{reply}' in {response_time} ms to {ui['full_name']} "
                                f"(@{ui['username']}) in {ui['chat_title']} [{ui['chat_id']}] {ui['chat_link']}"
                            )
                    else:
                        logger.warning(f"⚠️ Failed to send calculation result for '{original}'")
                        
                except Exception as send_error:
                    logger.error(f"❌ Error sending calculation result for '{original}': {send_error}")
                    logger.error(f"🔍 Send error traceback: {traceback.format_exc()}")
                    
            except InvalidExpression as invalid_error:
                logger.error(f"❌ Invalid expression attempted: '{original}' from {ui['full_name']} (@{ui['username']})")
                logger.error(f"🔍 Invalid expression details: {invalid_error}")
                
            except ZeroDivisionError as zero_div_error:
                logger.error(f"❌ Division by zero in expression '{original}' from {ui['full_name']} (@{ui['username']})")
                try:
                    await safe_send_message(
                        context.bot,
                        update.effective_chat.id,
                        MESSAGES['division_error'].format(expression=original),
                        reply_to=update.message.message_id if not is_private else None,
                    )
                except Exception as error_send_error:
                    logger.error(f"❌ Failed to send division by zero error: {error_send_error}")
                    
            except Exception as calc_error:
                logger.error(f"❌ Calculation error for '{original}' from {ui['full_name']} (@{ui['username']}): {calc_error}")
                logger.error(f"🔍 Calculation error traceback: {traceback.format_exc()}")
                    
    except Exception as e:
        logger.error(f"❌ Critical error in calculate_expression: {e}")
        logger.error(f"🔍 Critical error traceback: {traceback.format_exc()}")
        try:
            await update.message.reply_text(MESSAGES['unexpected_error'])
        except Exception as fallback_error:
            logger.error(f"❌ Failed to send fallback error message: {fallback_error}")

class DummyHandler(BaseHTTPRequestHandler):
    """HTTP health check handler"""
    def do_GET(self):
        client = self.client_address
        logger.info(f"🌐 Health check GET received from {client}")
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(MESSAGES['health_check'].encode())
            logger.info("✅ Health check response sent successfully")
        except Exception as e:
            logger.error(f"❌ Error handling health check GET: {e}")
            logger.error(f"🔍 Health check GET traceback: {traceback.format_exc()}")

    def do_HEAD(self):
        client = self.client_address
        logger.info(f"🌐 Health check HEAD received from {client}")
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            logger.info("✅ Health check HEAD response sent successfully")
        except Exception as e:
            logger.error(f"❌ Error handling health check HEAD: {e}")
            logger.error(f"🔍 Health check HEAD traceback: {traceback.format_exc()}")

    def log_message(self, format, *args):
        # Suppress default logging
        pass

def start_dummy_server():
    """Start HTTP health check server"""
    logger.info(f"🌐 Starting HTTP server on port {PORT}")
    try:
        server = HTTPServer(("0.0.0.0", PORT), DummyHandler)
        logger.info(f"✅ HTTP server listening on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ HTTP server error: {e}")
        logger.error(f"🔍 HTTP server traceback: {traceback.format_exc()}")
        logger.critical("🔥 HTTP server failed to start - this may affect deployment health checks")

def handle_exception(exc_type, exc_value, exc_traceback):
    """Global error handler for exceptions"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    logger.critical(
        f"🔥 Uncaught exception: {exc_type.__name__}: {exc_value}",
        exc_info=(exc_type, exc_value, exc_traceback)
    )

# Set global exception handler
sys.excepthook = handle_exception

def main():
    """Initialize and run the bot"""
    logger.info("🚀 Bot is starting...")
    
    # Environment validation
    if not BOT_TOKEN:
        logger.critical("🔒 Cannot start bot: BOT_TOKEN not set. Exiting.")
        return
    
    if not UPDATES_URL:
        logger.warning("⚠️ UPDATES_URL not set - updates button may not work")
    
    if not SUPPORT_URL:
        logger.warning("⚠️ SUPPORT_URL not set - support button may not work")
    
    try:
        logger.info("🔧 Building Telegram Application...")
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        logger.debug("🔧 Telegram Application built successfully")
        
        # Add handlers
        logger.info("➕ Adding command handlers...")
        app.add_handler(CommandHandler('start', start_command))
        logger.debug("➕ Added /start handler")
        
        app.add_handler(CommandHandler('help', help_command))
        logger.debug("➕ Added /help handler")
        
        app.add_handler(CommandHandler('ping', ping_command))
        logger.debug("➕ Added /ping handler")
        
        app.add_handler(CallbackQueryHandler(handle_callback_query))
        logger.debug("➕ Added callback query handler")
        
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, calculate_expression))
        logger.debug("➕ Added message handler for calculations")

        # Command setup function
        async def set_commands(app):
            try:
                logger.debug("🔧 Setting bot commands via API")
                await app.bot.set_my_commands(COMMANDS)
                logger.info("✅ Bot commands registered successfully 🎉  ➤ /start - Welcome  ➤ /help - How to use me")
            except Exception as cmd_error:
                logger.error(f"❌ Failed to set bot commands: {cmd_error}")
                logger.error(f"🔍 Commands setup traceback: {traceback.format_exc()}")

        app.post_init = set_commands
        logger.debug("🔧 post_init hook set for setting commands")

        # Error handler for application
        async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            logger.error(f"❌ Exception while handling an update: {context.error}")
            logger.error(f"🔍 Update error traceback: {traceback.format_exc()}")
            
            # Extract update info for debugging
            if isinstance(update, Update):
                try:
                    ui = extract_user_info(update)
                    logger.error(f"🔍 Error occurred for user: {ui['full_name']} (@{ui['username']}) [ID: {ui['user_id']}]")
                    
                    # Notify user about error in private chats
                    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
                        try:
                            await context.bot.send_message(
                                update.effective_chat.id,
                                MESSAGES['error_occurred']
                            )
                        except Exception as notify_error:
                            logger.error(f"❌ Failed to notify user about error: {notify_error}")
                            
                except Exception as extract_error:
                    logger.error(f"❌ Failed to extract user info during error handling: {extract_error}")

        # Add error handler
        app.add_error_handler(error_handler)
        logger.debug("➕ Added global error handler")

        # Start polling
        logger.info("📡 Starting bot polling...")
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        logger.info("📡 Bot polling started successfully")
        
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.critical(f"❌ Fatal error in main: {e}")
        logger.critical(f"🔍 Main function traceback: {traceback.format_exc()}")
        logger.critical("🔥 Bot crashed - check the logs above for details")
    finally:
        logger.info("🏁 Bot shutdown complete")

if __name__ == '__main__':
    try:
        logger.info("🧵 Starting health check server thread...")
        health_thread = threading.Thread(target=start_dummy_server, daemon=True)
        health_thread.start()
        logger.info("🧵 Health check server thread started successfully")
        
        # Small delay to ensure server starts
        time.sleep(0.5)
        
        # Start the main bot
        main()
        
    except KeyboardInterrupt:
        logger.info("🛑 Application stopped by user")
    except Exception as e:
        logger.critical(f"❌ Fatal error in __main__: {e}")
        logger.critical(f"🔍 Main execution traceback: {traceback.format_exc()}")
    finally:
        logger.info("🏁 Application shutdown complete")