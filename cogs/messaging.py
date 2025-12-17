# ./discord-radio-bot-modular/cogs/messaging.py

import discord
from discord import app_commands
from discord.ext import commands
import logging

from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.messaging')

class Messaging(commands.Cog):
    """Cog for simple message-related commands."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("Messaging Cog initialized.")

    @app_commands.command(name="say", description="Makes the bot say something in the current channel.")
    @app_commands.describe(message="The text you want the bot to say.")
    @app_commands.guild_only() # Restrict this command to servers
    @app_commands.checks.bot_has_permissions(send_messages=True)
    async def say(self, interaction: discord.Interaction, message: str):
        """
        Takes user input and sends it as a message from the bot, preventing pings.
        Only sends a followup message on failure.
        """
        # --- REVISED FLOW ---
        # 1. Acknowledge the command privately and temporarily.
        await interaction.response.send_message("Thinking...", ephemeral=True)

        # 2. Prepare the message with pings disabled.
        allowed_mentions = discord.AllowedMentions.none() # A simpler way to disable all pings

        try:
            # 3. Send the public message to the channel.
            await interaction.channel.send(message, allowed_mentions=allowed_mentions)
            
            # 4. On success, delete the initial "Thinking..." message. No success notification needed.
            await interaction.delete_original_response()

        except discord.HTTPException as e:
            # This catches potential sending errors.
            logger.error(f"Failed to send '/say' message in channel {interaction.channel.id}: {e}")
            # Edit the "Thinking..." message to show the error instead.
            await interaction.edit_original_response(content=f"❌ Failed to send message. An error occurred: {e}")


    @say.error
    async def say_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """A local error handler for the /say command for custom error messages."""
        error_message = "An unexpected error occurred. Please try again."

        # This will trigger if the @bot_has_permissions check fails.
        if isinstance(error, app_commands.BotMissingPermissions):
            error_message = "I don't have permission to send messages in this channel."
        else:
            logger.warning(f"Unhandled error in /say command: {error}")

        # Try to send or edit the response to show the error.
        if not interaction.response.is_done():
            await interaction.response.send_message(error_message, ephemeral=True)
        else:
            try:
                await interaction.edit_original_response(content=error_message)
            except discord.NotFound:
                # If the original "Thinking..." message was somehow deleted, send a new one.
                await interaction.followup.send(error_message, ephemeral=True)


# This function is called by discord.py when loading the extension.
async def setup(bot: RadioBot):
    await bot.add_cog(Messaging(bot))