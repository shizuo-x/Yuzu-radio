# ./discord-radio-bot-modular/run.py

import asyncio
import logging
import sys

# Import configuration and the custom Bot class
import config
from core.bot import RadioBot

# --- Logging Setup ---
# Configure logging level and format
log_formatter = logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s')
log_handler = logging.StreamHandler(sys.stdout) # Log to console
log_handler.setFormatter(log_formatter)

# Get the root logger and add the handler
root_logger = logging.getLogger()
root_logger.setLevel(config.LOG_LEVEL)
root_logger.addHandler(log_handler)

# Optionally add a file handler
# file_handler = logging.FileHandler('bot.log', encoding='utf-8', mode='w')
# file_handler.setFormatter(log_formatter)
# root_logger.addHandler(file_handler)

logger = logging.getLogger('discord_bot.run') # Logger for this specific file

async def main():
    """Main entry point for running the bot."""
    logger.info("Starting bot...")

    if not config.BOT_TOKEN:
        logger.critical("CRITICAL ERROR: DISCORD_TOKEN is not set in config or .env file.")
        return # Exit if no token

    # Create an instance of our custom Bot
    bot = RadioBot()

    try:
        # Start the bot using the token from config
        # The setup_hook in RadioBot will load cogs before logging in
        await bot.start(config.BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Shutting down.")
    except Exception as e:
        logger.critical(f"CRITICAL ERROR running bot: {e}", exc_info=True)
    finally:
        if not bot.is_closed():
            await bot.close() # Ensure cleanup runs
        logger.info("Bot process has shut down.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # This catches Ctrl+C before asyncio.run() fully starts perhaps
        logger.info("Shutdown initiated via KeyboardInterrupt.")