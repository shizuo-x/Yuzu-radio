# ./discord-radio-bot-modular/cogs/utility.py

import discord
from discord.ext import commands
import logging
import math
import asyncio

# Import configuration and the main bot class type hint
import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.utility')

# Constants for pagination
LIST_ITEMS_PER_PAGE = 10
HELP_TIMEOUT = 120.0

class Utility(commands.Cog):
    """Contains utility commands like help, ping, list."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("Utility Cog initialized.")

    @commands.hybrid_command(name="ping", description="Checks the bot's latency.")
    async def ping(self, ctx: commands.Context):
        latency = self.bot.latency * 1000
        await ctx.send(f"Pong! Latency: {latency:.2f} ms", ephemeral=True)

    # --- Paginated Help Command ---
    def get_help_page_content(self, page_num: int, total_pages: int, display_prefix: str) -> discord.Embed:
        """Creates the embed content for a specific help page."""
        embed = discord.Embed(color=config.DEFAULT_EMBED_COLOR)
        try: embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        except: pass

        # Page 1: General & Voice
        if page_num == 0:
            embed.title = f"{self.bot.user.name} Help (Page 1/{total_pages})"
            embed.description = f"Radio bot focused on 24/7 streams.\nCurrent Prefix: `{display_prefix}` (or @Mention)\nAlso supports Slash Commands (`/`)."
            embed.add_field(name="🔊 Voice Commands", value=f"**`{display_prefix}play <URL or Name>`** / `/play stream:<URL or Name>`\nPlays a radio stream.\n\n**`{display_prefix}stop`** / `/stop`\nStops playback.\n\n**`{display_prefix}leave`** / `{display_prefix}dc`\nDisconnects the bot.\n\n**`{display_prefix}now`** / `/now`\nShows current stream info.", inline=False)

        # Page 2: Utility & Emoji
        elif page_num == 1:
            embed.title = f"{self.bot.user.name} Help (Page 2/{total_pages})"
            embed.add_field(name="ℹ️ Utility Commands", value=f"**`{display_prefix}help`** / `/help`\nShows this message.\n\n**`{display_prefix}list`** / `/list`\nShows predefined streams.\n\n**`{display_prefix}ping`**\nChecks bot latency.", inline=False)
            embed.add_field(name="🖼️ Emoji Commands", value=f"**`{display_prefix}convert <URL> <Name>`** / `/convert link:<URL> name:<Name>`\nAdds a GIF as a server emoji.", inline=False)

        # Page 3: Translation Commands (NEW PAGE)
        elif page_num == 2:
            embed.title = f"{self.bot.user.name} Help (Page 3/{total_pages})"
            embed.description = "Translate messages from one channel to another, even across servers."
            embed.add_field(
                name="🌐 Translation Commands",
                value="`/translate_subscribe to_channel`\nSubscribes to a channel, sending translations to another channel (requires Admin permission).\n\n"
                      "`/translate_subscribe to_dm`\nSubscribes to a channel, sending translations to your DMs.\n\n"
                      "`/translate_list`\nPrivately lists your active subscriptions.\n\n"
                      "`/translate_unsubscribe <ID>`\nDeletes a subscription by its ID.",
                inline=False
            )

        # Page 4: Admin & Playback Control
        elif page_num == 3:
            embed.title = f"{self.bot.user.name} Help (Page 4/{total_pages})"
            embed.add_field(name="⚙️ Admin Commands", value=f"**`{display_prefix}setprefix <New Prefix>`** / `/setprefix new_prefix:<New Prefix>`\nChanges the prefix for this server. Use `reset` for default.", inline=False)
            embed.add_field(name="▶️ Playback Control", value=f"React with {config.STOP_REACTION} on the 'Now Playing' message to stop playback.", inline=False)

        embed.set_footer(text=f"Page {page_num + 1}/{total_pages}")
        return embed

    @commands.hybrid_command(name="help", description="Shows the bot's help information (paginated).")
    async def help(self, ctx: commands.Context):
        """Shows the bot's help information, paginated."""
        is_interaction = ctx.interaction is not None
        ephemeral = False
        if is_interaction: await ctx.defer(ephemeral=ephemeral)

        display_prefix = config.COMMAND_PREFIX
        if ctx.guild and str(ctx.guild.id) in self.bot.guild_prefixes:
             display_prefix = self.bot.guild_prefixes[str(ctx.guild.id)]
        elif ctx.prefix and not ctx.prefix.startswith(f'<@'):
             display_prefix = ctx.prefix

        # --- UPDATE TOTAL PAGES ---
        total_pages = 4
        current_page = 0

        initial_embed = self.get_help_page_content(current_page, total_pages, display_prefix)
        message = await ctx.send(embed=initial_embed, ephemeral=ephemeral)

        if is_interaction and not message:
             try: message = await ctx.interaction.original_response()
             except discord.NotFound: logger.error(f"[{ctx.guild_id if ctx.guild else 'DM'}] Failed to get original response for help."); await ctx.send("Failed pagination.", ephemeral=True); return

        if total_pages <= 1 or not message: return

        try: await message.add_reaction("◀️"); await message.add_reaction("▶️")
        except discord.Forbidden: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Missing Add Reactions for help pagination."); return
        except discord.NotFound: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Help message disappeared."); return

        def check(reaction, user): return (user.id == ctx.author.id and reaction.message.id == message.id and str(reaction.emoji) in ["◀️", "▶️"])

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=HELP_TIMEOUT, check=check)
                valid_move = False
                if str(reaction.emoji) == "▶️" and current_page < total_pages - 1: current_page += 1; valid_move = True
                elif str(reaction.emoji) == "◀️" and current_page > 0: current_page -= 1; valid_move = True
                if valid_move:
                    new_embed = self.get_help_page_content(current_page, total_pages, display_prefix)
                    await message.edit(embed=new_embed)
                try: await message.remove_reaction(reaction.emoji, user)
                except discord.Forbidden: pass
                if not valid_move: continue
            except asyncio.TimeoutError:
                logger.debug(f"[{ctx.guild.id if ctx.guild else 'DM'}] Help pagination timeout msg {message.id}")
                try:
                    await message.clear_reactions()
                    timeout_embed = message.embeds[0];
                    if timeout_embed: timeout_embed.set_footer(text=f"Page {current_page + 1}/{total_pages} (Pagination timed out)"); await message.edit(embed=timeout_embed)
                except: pass
                break
            except discord.NotFound: logger.warning(f"[{ctx.guild.id if ctx.guild else 'DM'}] Help message {message.id} deleted."); break
            except Exception as e: logger.exception(f"[{ctx.guild.id if ctx.guild else 'DM'}] Error during help pagination: {e}"); break
    
    # (The `list` command and its helper remain unchanged)
    def create_list_page_embed(self, page_num: int, total_pages: int, stream_keys: list[str]) -> discord.Embed:
        start_index = page_num * LIST_ITEMS_PER_PAGE
        end_index = start_index + LIST_ITEMS_PER_PAGE
        keys_on_page = stream_keys[start_index:end_index]
        display_prefix = config.COMMAND_PREFIX
        embed = discord.Embed(title="📻 Predefined Radio Streams", description=f"Use `{display_prefix}play <Name>` or `/play stream:<Name>`:", color=discord.Color.orange())
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
        if not stream_keys: await ctx.send("No predefined streams are configured.", ephemeral=True); return
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