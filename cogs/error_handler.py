# ./discord-radio-bot-modular/cogs/error_handler.py

import discord
from discord.ext import commands
from discord import app_commands
import logging

# Import configuration and the main bot class type hint
import config
from core.bot import RadioBot # For type hinting

logger = logging.getLogger('discord_bot.cogs.error_handler')

class ErrorHandler(commands.Cog):
    """Handles global command errors."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        # Set the handlers directly on the bot object or tree
        # Note: Setting tree error handler here might override bot's default if any
        bot.tree.error(self.on_app_command_error)
        logger.info("ErrorHandler Cog initialized and linked.")

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Listener for prefix command errors."""
        # Ignore CommandNotFound for cleaner logs
        if isinstance(error, commands.CommandNotFound):
            return

        # Handle missing arguments
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing argument: `{error.param.name}`. See `{config.COMMAND_PREFIX}help`.", delete_after=15)
        # Handle check failures (permissions, etc.)
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("You don't have permission or context to use this command.", delete_after=15)
        # Handle command invocation errors (errors inside the command code)
        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
            logger.error(f"Error in prefix command '{ctx.command}': {original}", exc_info=original)
            await ctx.send(f"An error occurred running `{ctx.command}`: ```py\n{original}\n```") # Show error to user
        # Log other errors
        else:
            logger.error(f"Unhandled prefix command error for '{ctx.command}': {error}", exc_info=error)
            # Optionally send a generic message
            # await ctx.send("An unexpected error occurred.")


    # Note: This decorator needs to be applied *outside* the Cog listener if used directly on bot.tree
    # We apply it in __init__ instead. If that causes issues, define it outside the class.
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Global error handler for slash commands."""
        if isinstance(error, app_commands.CommandInvokeError):
            original_error = error.original
            logger.error(f"Error in slash command '{interaction.command.name if interaction.command else 'Unknown'}': {original_error}", exc_info=original_error)
            error_message = f"An internal error occurred: ```py\n{original_error}\n```" # Show details
        elif isinstance(error, app_commands.CheckFailure):
            logger.warning(f"Check failed for slash command '{interaction.command.name}' by user {interaction.user}: {error}")
            error_message = "You don't have the necessary permissions or context to use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
             error_message = f"Command is on cooldown. Try again in {error.retry_after:.2f}s."
        # Add more specific app_commands errors as needed
        else:
            logger.error(f"Unhandled slash command error for '{interaction.command.name if interaction.command else 'Unknown'}': {error}", exc_info=error)
            error_message = "An unexpected error occurred while processing the command."

        # Try to respond ephemerally
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(error_message, ephemeral=True)
            else:
                await interaction.followup.send(error_message, ephemeral=True)
        except discord.NotFound: logger.warning(f"[{interaction.guild_id}] Interaction expired before error could be sent.")
        except discord.Forbidden: logger.warning(f"[{interaction.guild_id}] Missing permissions to send error message.")
        except Exception as e: logger.error(f"[{interaction.guild_id}] Failed to send error message itself: {e}")

# Setup function for discord.py to load the cog
async def setup(bot: RadioBot):
    await bot.add_cog(ErrorHandler(bot))