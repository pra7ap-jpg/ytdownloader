import logging
import os
import subprocess
import glob
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from flask import Flask, request # Import Flask for webhooks

# --- CONFIGURATION ---
# IMPORTANT: Replace '<YOUR_BOT_TOKEN>' with the token you get from @BotFather
# In the Render setup, you will securely set this via an environment variable.
BOT_TOKEN = "8255096238:AAEViSpXI0_VsOQ7KqbL2iyqnLDQtq5g7AY" 

# Define Webhook URL details
# The WEBHOOK_PORT is used by the Flask app. Render will set the actual PORT environment variable.
WEBHOOK_PORT = int(os.environ.get("PORT", 8080))
# The WEBHOOK_URL will be provided by Render. We set it later using the Telegram API.
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL", "") 

# Set up logging for debugging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app_flask = Flask(__name__)

# Telegram Application Setup (defined globally)
# Securely retrieve the token from the environment variable set by Render
actual_token = os.environ.get('BOT_TOKEN', BOT_TOKEN)
application = Application.builder().token(actual_token).build()

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
    """
    Downloads media from a YouTube URL using yt-dlp and reliably returns the file path.
    """
    # Use a fixed filename template to prevent issues with long/complex titles
    output_template = "downloads/%(id)s.%(ext)s"
    
    # yt-dlp command structure
    command = [
        "yt-dlp",
        "--output", output_template,
        "-f", format_spec,  # Format specifier (e.g., 'bestaudio' or 'worstvideo')
        "--print", "filepath", # Use --print filepath for reliable path output
        url
    ]
    
    try:
        # Create the downloads directory if it doesn't exist
        os.makedirs("downloads", exist_ok=True)
        
        logger.info(f"Starting download for URL: {url} with format: {format_spec}")
        
        # Execute yt-dlp command and capture output
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        
        # The file path is reliably printed to stdout via the "--print filepath" argument.
        # We take the last line, as yt-dlp often prints logs before the final output.
        downloaded_filepath = result.stdout.strip().split('\n')[-1]
        
        if os.path.exists(downloaded_filepath):
            logger.info(f"Successfully downloaded file: {downloaded_filepath}")
            return downloaded_filepath
        else:
            logger.error(f"Download reported success, but file not found at: {downloaded_filepath}. STDOUT: {result.stdout}")
            # Fallback: Search the downloads folder for any recently modified file
            files = glob.glob(os.path.join("downloads", "*"))
            if files:
                return max(files, key=os.path.getmtime)
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
    
    # Simple URL validation (can be more robust)
    if not ("youtube.com" in url or "youtu.be" in url):
        await update.message.reply_text("That doesn't look like a valid YouTube link. Please send a full YouTube URL.")
        return

    # Set appropriate format and Telegram send method
    if media_type == 'audio':
        # Best audio quality, converted to opus/m4a for small size
        format_spec = "bestaudio[ext=m4a]" 
        send_method = context.bot.send_audio
        size_limit_warning = "Audio files are generally small and should upload quickly."
    else: # video
        # Worst video format for minimum file size (e.g., 360p mp4 combined with best audio)
        format_spec = "worstvideo[ext=mp4]+bestaudio/best[ext=mp4]/mp4" 
        send_method = context.bot.send_video
        size_limit_warning = "⚠️ *Warning:* Video files may exceed the 50MB bot limit. If the upload fails, please try the `/audio` command instead."


    initial_message = await update.message.reply_text(
        f"⏳ Processing the link and downloading the {media_type}... This may take a minute, please wait.\n\n{size_limit_warning}",
        parse_mode='Markdown'
    )

    # 1. Download the file
    file_path = download_youtube_media(url, format_spec)
    
    # 2. Check and Upload the file
    if file_path and os.path.exists(file_path):
        file_size = os.path.getsize(file_path) / (1024 * 1024) # Size in MB
        
        if file_size > 50:
            # Handle large file gracefully
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
                # Use the appropriate Telegram send method (send_audio or send_video)
                with open(file_path, 'rb') as media_file:
                    await send_method(
                        chat_id=update.effective_chat.id,
                        # Pass the file content to the media parameter
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
        
        # 3. Clean up the downloaded file to save disk space (CRITICAL for free hosting)
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

# --- WEBHOOK SETUP (HTTP Endpoints) ---
@app_flask.route("/", methods=["GET", "POST"])
async def webhook_handler():
    """Handles incoming Telegram updates via Webhook."""
    if request.method == "POST":
        # Get the update from the request body
        update = Update.de_json(request.get_json(force=True), application.bot)
        # Process the update asynchronously
        await application.process_update(update)
        return "", 200
    return "Bot is running. Send POST requests to this URL.", 200

@app_flask.route('/set_webhook')
async def set_webhook():
    """Sets the Telegram Webhook URL."""
    # This route is optional but useful for verifying setup.
    if not WEBHOOK_URL:
        return "Error: WEBHOOK_URL not set. Check Render configuration.", 500
        
    s = await application.bot.set_webhook(url=WEBHOOK_URL)
    if s:
        return f"Webhook setup successful! URL: {WEBHOOK_URL}", 200
    else:
        return "Webhook setup failed.", 500

# --- MAIN EXECUTION ---
def main() -> None:
    """Start the bot in Webhook mode."""
    if actual_token == 'YOUR_BOT_TOKEN_HERE':
        logger.error("FATAL ERROR: BOT_TOKEN is not configured.")
        return

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("audio", audio_handler))
    application.add_handler(CommandHandler("video", video_handler))

    # Handles all other text messages as unknown
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info("Bot configured for Webhook mode. Starting Gunicorn server...")

if __name__ == '__main__':
    main()
    # Gunicorn (from Dockerfile) will run the Flask app via the CMD command.
    # For local testing, you would typically run app_flask.run(...) here.