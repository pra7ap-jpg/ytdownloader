import logging
import os
import subprocess
import glob
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from flask import Flask, request
import asyncio

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('BOT_TOKEN', "8255096238:AAEViSpXI0_VsOQ7KqbL2iyqnLDQtq5g7AY")
WEBHOOK_PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app_flask = Flask(__name__)

# Telegram Application Setup
actual_token = os.environ.get('BOT_TOKEN', BOT_TOKEN)
application = Application.builder().token(actual_token).build()

# Flag to track initialization
_initialized = False

# --- UTILITY FUNCTIONS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcoming message with instructions."""
    help_message = (
        "🎬 Welcome to the Free YouTube Downloader Bot!\n\n"
        "Here are the commands to get your video or audio:\n\n"
        "📺 */video* `<YouTube URL>` - Downloads and sends the *lowest quality* video (MP4) for quick clips.\n"
        "🎵 */audio* `<YouTube URL>` - Downloads and sends the best audio (MP3/M4A) of the video. (Recommended for reliability)\n\n"
        "⚠️ *A Note on File Limits (CRITICAL):*\n"
        "This bot uses free hosting, which imposes strict file size limits (usually ~50MB). "
        "For long videos, the `/video` command will likely fail. *The `/audio` command is the safest choice*.\n"
    )
    await update.message.reply_text(help_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the same instruction message as /start."""
    await start(update, context)

def download_youtube_media(url: str, format_spec: str) -> str | None:
    """Downloads media from a YouTube URL using yt-dlp."""
    output_template = "downloads/%(id)s.%(ext)s"
    
    command = [
        "yt-dlp",
        "--output", output_template,
        "-f", format_spec,
        "--print", "filepath",
        # Add these flags to bypass YouTube's bot detection
        "--extractor-args", "youtube:player_client=android",
        "--no-check-certificate",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        url
    ]
    
    try:
        os.makedirs("downloads", exist_ok=True)
        logger.info(f"Starting download for URL: {url} with format: {format_spec}")
        
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        downloaded_filepath = result.stdout.strip().split('\n')[-1]
        
        if os.path.exists(downloaded_filepath):
            logger.info(f"Successfully downloaded file: {downloaded_filepath}")
            return downloaded_filepath
        else:
            logger.error(f"Download reported success, but file not found at: {downloaded_filepath}")
            files = glob.glob(os.path.join("downloads", "*"))
            if files:
                return max(files, key=os.path.getmtime)
            return None
        
    except subprocess.TimeoutExpired:
        logger.error(f"yt-dlp timed out after 120 seconds")
        return None
    except subprocess.CalledProcessError as e:
        logger.error(f"yt-dlp failed. STDERR: {e.stderr}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return None

# --- HANDLER FUNCTIONS ---

async def handle_download_request(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str) -> None:
    """Generic handler for /audio and /video commands."""
    
    if not context.args:
        await update.message.reply_text(f"Please provide a YouTube link after the /{media_type} command. E.g., `/{media_type} <link>`")
        return

    url = context.args[0]
    
    if not ("youtube.com" in url or "youtu.be" in url):
        await update.message.reply_text("That doesn't look like a valid YouTube link. Please send a full YouTube URL.")
        return

    if media_type == 'audio':
        format_spec = "bestaudio[ext=m4a]" 
        send_method = context.bot.send_audio
        size_limit_warning = "Audio files are generally small and should upload quickly."
    else:
        format_spec = "worstvideo[ext=mp4]+bestaudio/best[ext=mp4]/mp4" 
        send_method = context.bot.send_video
        size_limit_warning = "⚠️ *Warning:* Video files may exceed the 50MB bot limit. If the upload fails, please try the `/audio` command instead."

    initial_message = await update.message.reply_text(
        f"⏳ Processing the link and downloading the {media_type}... This may take a minute, please wait.\n\n{size_limit_warning}",
        parse_mode='Markdown'
    )

    file_path = download_youtube_media(url, format_spec)
    
    if file_path and os.path.exists(file_path):
        file_size = os.path.getsize(file_path) / (1024 * 1024)
        
        if file_size > 50:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=initial_message.message_id,
                text=(
                    "❌ *Download Failed: File Too Large*\n"
                    f"The requested file is {file_size:.2f} MB. Free bot hosting restricts uploads to 50MB.\n"
                    "Please try the `/audio` command for smaller, audio-only files."
                ),
                parse_mode='Markdown'
            )
        else:
            try:
                with open(file_path, 'rb') as media_file:
                    await send_method(
                        chat_id=update.effective_chat.id,
                        media=media_file, 
                        caption=f"✅ Download complete! Here is your {media_type} file."
                    )
                await context.bot.delete_message(update.effective_chat.id, initial_message.message_id)
            except Exception as e:
                logger.error(f"Telegram Upload Failed: {e}")
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=initial_message.message_id,
                    text="❌ *Upload Failed.*\nAn error occurred while sending the file to Telegram. The file might still be too large or there was a network issue.",
                    parse_mode='Markdown'
                )
        
        os.remove(file_path)
        logger.info(f"Cleaned up file: {file_path}")
    else:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=initial_message.message_id,
            text="❌ *Download Failed.*\nCould not download the content. Check the link, or if the video is restricted/private."
        )

async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /audio command."""
    await handle_download_request(update, context, 'audio')

async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /video command."""
    await handle_download_request(update, context, 'video')

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responds to unknown commands."""
    await update.message.reply_text("Sorry, I don't recognize that command. Use /start or /help to see my functions.")

# --- INITIALIZATION FUNCTION ---
async def ensure_initialized():
    """Ensure the application is initialized (lazy initialization)."""
    global _initialized
    if not _initialized:
        # Register handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("audio", audio_handler))
        application.add_handler(CommandHandler("video", video_handler))
        application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
        
        # Initialize and start
        await application.initialize()
        await application.start()
        _initialized = True
        logger.info("Telegram application initialized successfully")

# --- WEBHOOK SETUP ---
@app_flask.route("/", methods=["GET", "POST"])
def webhook_handler():
    """Handles incoming Telegram updates via Webhook."""
    if request.method == "POST":
        try:
            # Create new event loop for this request
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Ensure application is initialized
            loop.run_until_complete(ensure_initialized())
            
            # Process the update
            update = Update.de_json(request.get_json(force=True), application.bot)
            loop.run_until_complete(application.process_update(update))
            
            loop.close()
            return "", 200
        except Exception as e:
            logger.error(f"Error processing update: {e}", exc_info=True)
            return "", 500
    return "Bot is running. Send POST requests to this URL.", 200

@app_flask.route("/test_ytdlp")
def test_ytdlp():
    """Test if yt-dlp is working."""
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        return f"yt-dlp version: {result.stdout}", 200
    except Exception as e:
        return f"yt-dlp not found: {e}", 500

if __name__ == '__main__':
    logger.info("Bot configured for Webhook mode. Starting server...")
