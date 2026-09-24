import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)
from config import config
from bot.handler import (
    start_command,
    help_command,
    pause_command,
    resume_command,
    settings_command,
    frequency_command,
    quiethours_command,
    profile_command,
    status_command,
    forget_command,
    handle_message,
    error_handler,
)
from core.proactive import setup_proactive_jobs

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    """Start the bot."""
    if not config.TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set. Create a .env file.")
        return

    # Build the application
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )

    # ── Command Handlers ──
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("pause", pause_command))
    application.add_handler(CommandHandler("resume", resume_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("frequency", frequency_command))
    application.add_handler(CommandHandler("quiethours", quiethours_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("forget", forget_command))

    # ── Message Handler ──
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # ── Error Handler ──
    application.add_error_handler(error_handler)

    # ── Proactive Outreach Jobs ──
    setup_proactive_jobs(application)

    # ── Start ──
    print(f"🤖 {config.BOT_NAME} is starting...")
    print(f"   Model: {config.MODEL}")
    print(f"   Proactive check interval: {config.PROACTIVE_CHECK_INTERVAL}s")
    print(f"   Database: {config.DB_PATH}")
    print(f"   Ready to reach out! 💙")

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
