# ./discord-radio-bot-modular/cogs/utility.py

import discord
from discord.ext import commands
import logging
import math
import asyncio

# Import configuration and the main bot class type hint
import config
from core.bot import RadioBot # For type hinting

logger = logging.getLogger('discord_bot.cogs.utility')

ITEMS_PER_PAGE = 10 # How many radio streams to show on each page

class Utility(commands.Cog):
    """Contains utility commands like help and ping."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("Utility Cog initialized.")

    # (ping and help commands remain unchanged)
    @commands.hybrid_command(name="ping", description="Checks the bot's latency.")
    async def ping(self, ctx: commands.Context):
        """Checks the bot's latency."""
        latency = self.bot.latency * 1000
        await ctx.send(f"Pong! Latency: {latency:.2f} ms", ephemeral=True)

    @commands.hybrid_command(name="help", description="Shows the bot's help information.")
    async def help(self, ctx: commands.Context):
        """Shows the bot's help information."""
        is_interaction = ctx.interaction is not None
        ephemeral = is_interaction # Help is ephemeral for slash commands
        if is_interaction: await ctx.defer(ephemeral=ephemeral) # Defer needed if ephemeral

        embed = discord.Embed(
            title=f"{self.bot.user.name} Help",
            description=f"Radio bot. Prefix: `{config.COMMAND_PREFIX}`. Also uses Slash Commands.",
            color=config.DEFAULT_EMBED_COLOR
        )
        try: embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        except: pass
        embed.add_field( name="🔊 Voice Commands", value=f"`{config.COMMAND_PREFIX}play <URL or Name>` or `/play stream:<URL or Name>`\nPlays a live radio stream from URL or `{config.COMMAND_PREFIX}list`.\n\n`{config.COMMAND_PREFIX}stop` or `/stop`\nStops playback.\n\n`{config.COMMAND_PREFIX}leave` or `{config.COMMAND_PREFIX}dc`\nDisconnects the bot.\n\n`{config.COMMAND_PREFIX}now` or `/now`\nShows the current stream info.", inline=False)
        embed.add_field( name="ℹ️ Utility Commands", value=f"`{config.COMMAND_PREFIX}help` or `/help`\nShows this message.\n\n`{config.COMMAND_PREFIX}list` or `/list`\nShows predefined streams.\n\n`{config.COMMAND_PREFIX}ping`\nChecks latency.", inline=False)
        embed.add_field( name="▶️ Playback Control", value=f"React with {config.STOP_REACTION} on the 'Now Playing' message to stop.", inline=False)
        embed.set_footer(text="Enjoy!")

        await ctx.send(embed=embed, ephemeral=ephemeral)

    # --- Paginated List Command ---

    # --- MODIFIED create_list_page_embed ---
    def create_list_page_embed(self, page_num: int, total_pages: int, stream_keys: list[str]) -> discord.Embed:
        """Helper function to create an embed for a specific list page."""
        start_index = page_num * ITEMS_PER_PAGE
        end_index = start_index + ITEMS_PER_PAGE
        keys_on_page = stream_keys[start_index:end_index]

        # Use description field for preamble, value field for list
        embed = discord.Embed(
            title="📻 Predefined Radio Streams",
            description=f"Use `{config.COMMAND_PREFIX}play <Name>` or `/play stream:<Name>`:",
            color=discord.Color.orange()
        )

        if not keys_on_page:
            embed.add_field(name="Streams", value="*No streams on this page.*", inline=False)
        else:
            list_content = ""
            for i, key in enumerate(keys_on_page, start=start_index):
                # Retrieve the stream data dictionary using the key
                stream_data = config.PREDEFINED_STREAMS.get(key, {})
                # --- GET DESCRIPTION ---
                description = stream_data.get("desc", "No description") # Get description, fallback
                # --- FORMAT WITH DESCRIPTION ---
                # Format: Number. `Name` - Description
                list_content += f"**{i+1}.** `{key}` - *{description}*\n"

            # Add the formatted list as a single field value
            # Ensure content doesn't exceed embed field limits (1024 chars)
            if len(list_content) > 1024:
                list_content = list_content[:1020] + "\n..." # Truncate if too long

            embed.add_field(name="Available Streams", value=list_content, inline=False)


        embed.set_footer(text=f"Page {page_num + 1}/{total_pages}") # User-facing page numbers start at 1
        return embed
    # --- END MODIFIED create_list_page_embed ---

    @commands.hybrid_command(name="list", description="Shows the list of predefined radio streams.")
    async def list(self, ctx: commands.Context):
        """Shows the list of predefined radio streams, paginated."""
        is_interaction = ctx.interaction is not None
        ephemeral = False
        if is_interaction: await ctx.defer(ephemeral=ephemeral)

        stream_keys = list(config.PREDEFINED_STREAMS.keys())

        if not stream_keys:
            await ctx.send("No predefined streams are configured.", ephemeral=True)
            return

        total_pages = math.ceil(len(stream_keys) / ITEMS_PER_PAGE)
        current_page = 0

        initial_embed = self.create_list_page_embed(current_page, total_pages, stream_keys)
        # Send the initial message - use ctx.send() which works for both contexts
        # For interactions, ctx.send is mapped to followup if deferred.
        message = await ctx.send(embed=initial_embed, ephemeral=ephemeral)
        # If it was an interaction and we didn't defer, message might be None.
        # A better approach for hybrid commands might be to get the original response message.
        # Let's try getting the message explicitly if interaction.
        if is_interaction and not message:
             try:
                 message = await ctx.interaction.original_response()
             except discord.errors.NotFound:
                 logger.error(f"[{ctx.guild_id}] Failed to get original response message for paginated list.")
                 await ctx.send("Failed to start pagination.", ephemeral=True)
                 return


        if total_pages <= 1 or not message: # Exit if no pages or message sending failed
            return

        try:
            await message.add_reaction("◀️")
            await message.add_reaction("▶️")
        except discord.Forbidden:
            logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Missing Add Reactions permission for pagination on list command.")
            return

        def check(reaction, user):
            return (user.id == ctx.author.id and
                    reaction.message.id == message.id and
                    str(reaction.emoji) in ["◀️", "▶️"])

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=120.0, check=check)

                if str(reaction.emoji) == "▶️" and current_page < total_pages - 1:
                    current_page += 1
                elif str(reaction.emoji) == "◀️" and current_page > 0:
                    current_page -= 1
                else:
                    try: await message.remove_reaction(reaction.emoji, user)
                    except discord.Forbidden: pass
                    continue

                new_embed = self.create_list_page_embed(current_page, total_pages, stream_keys)
                await message.edit(embed=new_embed)

                try: await message.remove_reaction(reaction.emoji, user)
                except discord.Forbidden: pass

            except asyncio.TimeoutError:
                logger.debug(f"[{ctx.guild.id if ctx.guild else 'DM'}] Pagination timeout for list command message {message.id}")
                try:
                    await message.clear_reactions()
                    timeout_embed = message.embeds[0]
                    timeout_embed.set_footer(text=f"Page {current_page + 1}/{total_pages} (Pagination timed out)")
                    await message.edit(embed=timeout_embed)
                except discord.Forbidden: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Missing permissions to clear reactions/edit on timeout.")
                except Exception as e: logger.error(f"[{ctx.guild.id if ctx.guild else 'DM'}] Error cleaning up pagination on timeout: {e}")
                break
            except discord.NotFound: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Pagination message {message.id} deleted."); break
            except Exception as e: logger.exception(f"[{ctx.guild.id if ctx.guild else 'DM'}] Error during pagination loop: {e}"); break


# Setup function for discord.py to load the cog
async def setup(bot: RadioBot):
    await bot.add_cog(Utility(bot))