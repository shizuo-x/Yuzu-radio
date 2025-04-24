# ./discord-radio-bot-modular/cogs/admin_settings.py

import discord
from discord.ext import commands
import logging

# Import configuration and the main bot class type hint
import config
from core.bot import RadioBot # For type hinting

logger = logging.getLogger('discord_bot.cogs.admin_settings')

# Define maximum prefix length
MAX_PREFIX_LENGTH = 5

class AdminSettings(commands.Cog):
    """Commands for server administrators to configure the bot."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("AdminSettings Cog initialized.")

    @commands.hybrid_command(name="setprefix", description="Sets the command prefix for this server.")
    @commands.guild_only() # Ensure command is run in a server
    @commands.has_permissions(administrator=True) # Only administrators can run this
    @discord.app_commands.describe(new_prefix="The new prefix to use (e.g., 'r!', '$'). Use 'reset' to restore default.")
    async def setprefix(self, ctx: commands.Context, *, new_prefix: str):
        """Sets the command prefix for this server (Admin only)."""
        if not ctx.guild: return # Should be caught by guild_only

        guild_id_str = str(ctx.guild.id)
        cleaned_prefix = new_prefix.strip() # Remove leading/trailing whitespace

        # Handle resetting to default
        if cleaned_prefix.lower() == 'reset':
            if guild_id_str in self.bot.guild_prefixes:
                del self.bot.guild_prefixes[guild_id_str] # Remove custom prefix
                self.bot.save_prefixes() # Save changes
                await ctx.send(f"Prefix reset to default: `{config.COMMAND_PREFIX}`", ephemeral=True)
                logger.info(f"[{ctx.guild.id}] Prefix reset to default by {ctx.author}.")
            else:
                await ctx.send(f"Prefix is already the default: `{config.COMMAND_PREFIX}`", ephemeral=True)
            return

        # --- Validation ---
        if not cleaned_prefix:
            await ctx.send("Error: Prefix cannot be empty or just whitespace.", ephemeral=True)
            return
        if len(cleaned_prefix) > MAX_PREFIX_LENGTH:
            await ctx.send(f"Error: Prefix cannot be longer than {MAX_PREFIX_LENGTH} characters.", ephemeral=True)
            return
        if cleaned_prefix.startswith('/'):
            await ctx.send("Error: Prefix cannot start with `/` (reserved for slash commands).", ephemeral=True)
            return
        # Avoid prefixes that might conflict with mentions - simple check for starting with <@
        if cleaned_prefix.startswith('<@'):
             await ctx.send("Error: Prefix cannot start with `<@`.", ephemeral=True)
             return
        # Add any other validation rules needed (e.g., disallowed characters?)

        # --- Update and Save ---
        self.bot.guild_prefixes[guild_id_str] = cleaned_prefix
        self.bot.save_prefixes() # Persist the change

        logger.info(f"[{ctx.guild.id}] Prefix changed to '{cleaned_prefix}' by {ctx.author}.")
        await ctx.send(f"Prefix for this server successfully changed to: `{cleaned_prefix}`", ephemeral=True)


    @setprefix.error
    async def setprefix_error(self, ctx: commands.Context, error: commands.CommandError):
        """Error handler specifically for the setprefix command."""
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need administrator permissions to change the bot's prefix.", ephemeral=True)
        elif isinstance(error, commands.MissingRequiredArgument):
             await ctx.send(f"You need to provide a new prefix! Example: `{ctx.prefix}{ctx.command.name} r!` or `{ctx.prefix}{ctx.command.name} reset`", ephemeral=True)
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("This command can only be used inside a server.", ephemeral=True)
        else:
            # Let the global error handler in ErrorHandler Cog deal with other errors
            logger.debug(f"Unhandled error in setprefix, passing to global handler: {error}")
            # Re-raise the error for the global handler (or handle it fully here)
            # raise error


# Setup function for discord.py to load the cog
async def setup(bot: RadioBot):
    await bot.add_cog(AdminSettings(bot))