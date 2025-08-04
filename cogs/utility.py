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

# Constants for pagination
LIST_ITEMS_PER_PAGE = 10
HELP_TIMEOUT = 120.0 # Seconds for pagination timeout

class Utility(commands.Cog):
    """Contains utility commands like help, ping, list."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("Utility Cog initialized.")

    # --- Ping Command ---
    @commands.hybrid_command(name="ping", description="Checks the bot's latency.")
    async def ping(self, ctx: commands.Context):
        """Checks the bot's latency."""
        latency = self.bot.latency * 1000
        await ctx.send(f"Pong! Latency: {latency:.2f} ms", ephemeral=True)

    # --- Paginated Help Command ---

    def get_help_page_content(self, page_num: int, total_pages: int, display_prefix: str) -> discord.Embed:
        """Creates the embed content for a specific help page."""
        embed = discord.Embed(color=config.DEFAULT_EMBED_COLOR)
        try: embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        except: pass # Ignore if avatar fails

        # Page 1: General & Voice
        if page_num == 0:
            embed.title = f"{self.bot.user.name} Help (Page 1/{total_pages})"
            embed.description = f"Radio bot focused on 24/7 streams.\n" \
                                f"Current Prefix: `{display_prefix}` (or @Mention)\n" \
                                f"Also supports Slash Commands (`/`)."
            embed.add_field(
                name="🔊 Voice Commands",
                value=f"**`{display_prefix}play <URL or Name>`** / `/play stream:<URL or Name>`\n"
                      f"Plays a live radio stream from URL or predefined name (see `{display_prefix}list`).\n\n"
                      f"**`{display_prefix}stop`** / `/stop`\n"
                      f"Stops the current playback.\n\n"
                      f"**`{display_prefix}leave`** / `{display_prefix}dc`\n"
                      f"Disconnects the bot from the voice channel.\n\n"
                      f"**`{display_prefix}now`** / `/now`\n"
                      f"Shows the currently playing stream information again.",
                inline=False
            )

        # Page 2: Utility & Emoji
        elif page_num == 1:
            embed.title = f"{self.bot.user.name} Help (Page 2/{total_pages})"
            embed.add_field(
                name="ℹ️ Utility Commands",
                value=f"**`{display_prefix}help`** / `/help`\n"
                      f"Shows this help message.\n\n"
                      f"**`{display_prefix}list`** / `/list`\n"
                      f"Shows the list of predefined radio stream names.\n\n"
                      f"**`{display_prefix}ping`**\n"
                      f"Checks the bot's latency to Discord.",
                inline=False
            )
            embed.add_field(
                name="🖼️ Emoji Commands", # New Section
                value=f"**`{display_prefix}convert <URL> <Name>`** / `/convert link:<URL> name:<Name>`\n"
                      f"Downloads a GIF from the URL, resizes it, and adds it as a server emoji with the given name (requires Bot & User to have 'Manage Expressions' permission).",
                inline=False
            )

        # Page 3: Admin & Playback Control
        elif page_num == 2:
            embed.title = f"{self.bot.user.name} Help (Page 3/{total_pages})"
            embed.add_field(
                name="⚙️ Admin Commands",
                 value=f"**`{display_prefix}setprefix <New Prefix>`** / `/setprefix new_prefix:<New Prefix>`\n"
                       f"Changes the command prefix for this server (Admin only).\nUse `reset` to restore default (`{config.COMMAND_PREFIX}`).",
                inline=False
            )
            embed.add_field(
                name="▶️ Playback Control",
                value=f"React with {config.STOP_REACTION} on the 'Now Playing' message to stop playback.",
                inline=False
            )
        
        # Fallback for invalid page number
        else:
             embed.title = f"{self.bot.user.name} Help (Invalid Page)"
             embed.description = "Something went wrong."

        embed.set_footer(text=f"Page {page_num + 1}/{total_pages}")
        return embed


    @commands.hybrid_command(name="help", description="Shows the bot's help information (paginated).")
    async def help(self, ctx: commands.Context):
        """Shows the bot's help information, paginated."""
        is_interaction = ctx.interaction is not None
        
        # --- FIX: Set ephemeral to False for pagination to work ---
        ephemeral = False # Pagination requires a public message
        
        if is_interaction: await ctx.defer(ephemeral=ephemeral) # Defer publicly

        # Determine the prefix to display in examples
        display_prefix = config.COMMAND_PREFIX # Default
        if ctx.guild and str(ctx.guild.id) in self.bot.guild_prefixes:
             display_prefix = self.bot.guild_prefixes[str(ctx.guild.id)]
        elif ctx.prefix and not ctx.prefix.startswith(f'<@'): # Use invoked prefix if available and not a mention
             display_prefix = ctx.prefix

        # --- Pagination Setup ---
        total_pages = 3 # Currently 3 defined pages
        current_page = 0

        initial_embed = self.get_help_page_content(current_page, total_pages, display_prefix)
        message = await ctx.send(embed=initial_embed, ephemeral=ephemeral)

        # Get message object for interactions if ctx.send didn't return it
        if is_interaction and not message:
             try: message = await ctx.interaction.original_response()
             except discord.NotFound: logger.error(f"[{ctx.guild_id if ctx.guild else 'DM'}] Failed to get original response message for help."); await ctx.send("Failed to start pagination.", ephemeral=True); return

        if total_pages <= 1 or not message: return # Exit if only 1 page or message failed

        try: await message.add_reaction("◀️"); await message.add_reaction("▶️")
        except discord.Forbidden: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Missing Add Reactions permission for help pagination."); return
        except discord.NotFound: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Help message disappeared before reactions added."); return

        def check(reaction, user): return (user.id == ctx.author.id and reaction.message.id == message.id and str(reaction.emoji) in ["◀️", "▶️"])

        # --- Pagination Loop ---
        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=HELP_TIMEOUT, check=check)
                valid_move = False
                if str(reaction.emoji) == "▶️" and current_page < total_pages - 1: current_page += 1; valid_move = True
                elif str(reaction.emoji) == "◀️" and current_page > 0: current_page -= 1; valid_move = True

                if valid_move:
                    new_embed = self.get_help_page_content(current_page, total_pages, display_prefix)
                    await message.edit(embed=new_embed)

                # Remove user's reaction (requires Manage Messages)
                try: await message.remove_reaction(reaction.emoji, user)
                except discord.Forbidden: pass
                # Only continue loop if the move was invalid (no need to edit)
                if not valid_move: continue

            except asyncio.TimeoutError:
                logger.debug(f"[{ctx.guild.id if ctx.guild else 'DM'}] Help pagination timeout msg {message.id}")
                try:
                    await message.clear_reactions()
                    timeout_embed = message.embeds[0]
                    if timeout_embed: timeout_embed.set_footer(text=f"Page {current_page + 1}/{total_pages} (Pagination timed out)"); await message.edit(embed=timeout_embed)
                except: pass # Ignore cleanup errors
                break # Exit loop on timeout
            except discord.NotFound: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Help message {message.id} deleted."); break
            except Exception as e: logger.exception(f"[{ctx.guild.id if ctx.guild else 'DM'}] Error during help pagination: {e}"); break


    # --- Paginated List Command (Unchanged) ---
    def create_list_page_embed(self, page_num: int, total_pages: int, stream_keys: list[str]) -> discord.Embed:
        start_index = page_num * LIST_ITEMS_PER_PAGE
        end_index = start_index + LIST_ITEMS_PER_PAGE
        keys_on_page = stream_keys[start_index:end_index]
        display_prefix = config.COMMAND_PREFIX

        embed = discord.Embed(
            title="📻 Predefined Radio Streams",
            description=f"Use `{display_prefix}play <Name>` or `/play stream:<Name>`:",
            color=discord.Color.orange()
        )
        if not keys_on_page: embed.add_field(name="Streams", value="*No streams on this page.*", inline=False)
        else:
            list_content = ""
            for i, key in enumerate(keys_on_page, start=start_index):
                stream_data = config.PREDEFINED_STREAMS.get(key, {})
                description = stream_data.get("desc", "No description")
                list_content += f"**{i+1}.** `{key}` - *{description}*\n"
            if len(list_content) > 1024: list_content = list_content[:1020] + "\n..."
            embed.add_field(name="Available Streams", value=list_content, inline=False)
        embed.set_footer(text=f"Page {page_num + 1}/{total_pages}")
        return embed

    @commands.hybrid_command(name="list", description="Shows the list of predefined radio streams.")
    async def list(self, ctx: commands.Context):
        is_interaction = ctx.interaction is not None
        ephemeral = False
        if is_interaction: await ctx.defer(ephemeral=ephemeral)

        stream_keys = list(config.PREDEFINED_STREAMS.keys())
        if not stream_keys: await ctx.send("No predefined streams configured.", ephemeral=True); return

        total_pages = math.ceil(len(stream_keys) / LIST_ITEMS_PER_PAGE)
        current_page = 0

        initial_embed = self.create_list_page_embed(current_page, total_pages, stream_keys)
        message = await ctx.send(embed=initial_embed, ephemeral=ephemeral)
        if is_interaction and not message:
             try: message = await ctx.interaction.original_response()
             except discord.NotFound: logger.error(f"[{ctx.guild_id if ctx.guild else 'DM'}] Failed original response for list."); await ctx.send("Failed pagination.", ephemeral=True); return

        if total_pages <= 1 or not message: return

        try: await message.add_reaction("◀️"); await message.add_reaction("▶️")
        except discord.Forbidden: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Missing Add Reactions for list."); return

        def check(reaction, user): return (user.id == ctx.author.id and reaction.message.id == message.id and str(reaction.emoji) in ["◀️", "▶️"])

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=HELP_TIMEOUT, check=check)
                valid_move = False
                if str(reaction.emoji) == "▶️" and current_page < total_pages - 1: current_page += 1; valid_move = True
                elif str(reaction.emoji) == "◀️" and current_page > 0: current_page -= 1; valid_move = True

                if valid_move:
                    new_embed = self.create_list_page_embed(current_page, total_pages, stream_keys)
                    await message.edit(embed=new_embed)

                try: await message.remove_reaction(reaction.emoji, user)
                except discord.Forbidden: pass
                if not valid_move: continue

            except asyncio.TimeoutError:
                logger.debug(f"[{ctx.guild.id if ctx.guild else 'DM'}] List pagination timeout msg {message.id}")
                try:
                    await message.clear_reactions()
                    timeout_embed = message.embeds[0]; timeout_embed.set_footer(text=f"Page {current_page + 1}/{total_pages} (Pagination timed out)"); await message.edit(embed=timeout_embed)
                except: pass
                break
            except discord.NotFound: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] List message {message.id} deleted."); break
            except Exception as e: logger.exception(f"[{ctx.guild.id if ctx.guild else 'DM'}] Error during list pagination: {e}"); break

# Setup function for discord.py to load the cog
async def setup(bot: RadioBot):
    await bot.add_cog(Utility(bot))