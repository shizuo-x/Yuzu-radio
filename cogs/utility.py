# ./discord-radio-bot-modular/cogs/utility.py

import discord
from discord.ext import commands
import logging
import math
import asyncio

import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.utility')

LIST_ITEMS_PER_PAGE = 10
HELP_TIMEOUT = 120.0

class Utility(commands.Cog):
    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("Utility Cog initialized.")

    @commands.hybrid_command(name="ping", description="Checks the bot's latency.")
    async def ping(self, ctx: commands.Context):
        await ctx.send(f"Pong! Latency: {self.bot.latency * 1000:.2f} ms", ephemeral=True)

    # --- FULLY REVISED HELP COMMAND ---

    def get_help_page_content(self, page_num: int, total_pages: int, prefix: str) -> discord.Embed:
        """Creates the rich embed for a specific help page with detailed descriptions."""
        embed = discord.Embed(color=config.DEFAULT_EMBED_COLOR)
        try:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        except:
            pass
        
        # Determine the bot's name to use in examples
        bot_name = self.bot.user.name if self.bot.user else "Yuzu"

        # Page 1: Introduction & Radio
        if page_num == 0:
            embed.title = f"🎧 {bot_name} Help: Radio & Playback"
            embed.description = (f"Hi! I'm {bot_name}, your 24/7 radio companion.\n\n"
                                 f"**Prefix:** `{prefix}` or use Slash Commands (`/`).")
            embed.add_field(name="📻 Radio Commands", value=(
                f"**`/list`** / `{prefix}list`\n› **Browse available radio stations.**\n\n"
                f"**`/play <query>`** / `{prefix}play <query>`\n› Play a station by **Name** or **Number**.\n› *Example:* `/play 1` or `/play lofi`\n\n"
                f"**`/now`** / `{prefix}now`\n› See what's currently playing.\n\n"
                f"**`/stop`** / `{prefix}stop`\n› Stop playback.\n\n"
                f"**`/leave`** / `{prefix}dc`\n› Disconnect me from the voice channel."), inline=False)
            embed.add_field(name="▶️ Controls", value=f"React with {config.STOP_REACTION} on the player message to stop playback.", inline=False)

        # Page 2: AI Assistant
        elif page_num == 1:
            embed.title = f"🤖 {bot_name} Help: AI Assistant"
            embed.description = f"Chat with me directly! Just mention me (`@Bot`) to start a conversation."
            embed.add_field(name="How to Use", value=(
                "I remember the last few messages, so you can ask follow-up questions!\n\n"
                f"**Example:** `@{bot_name} What is the capital of France?`\n"
                f"**Example:** `@{bot_name} Tell me a joke!`"
            ), inline=False)
            embed.add_field(name="⚠️ Privacy", value="Messages mentioned to me are sent to the AI provider (Google Gemini) for processing.", inline=False)

        # Page 3: Reminders
        elif page_num == 2:
            embed.title = f"⏰ {bot_name} Help: Reminders"
            embed.description = "Never forget a thing! Set personal or server-wide reminders."
            embed.add_field(name="User Commands (Slash Only)", value=(
                "**`/remind <message> <time> <date> ...`**\n"
                "› Set a new reminder. Supports timezones and recurring options.\n\n"
                "**`/reminders list`**\n"
                "› View your upcoming reminders.\n\n"
                "**`/reminders delete <id>`**\n"
                "› Delete a reminder by its ID."
            ), inline=False)
            embed.add_field(name="Admin Config", value=(
                "**`/reminders_admin set_role`** & **`set_channel`**\n"
                "› Configure who can set reminders and where."
            ), inline=False)
            
        # Page 4: Confessions
        elif page_num == 3:
            embed.title = f"💌 {bot_name} Help: Confessions"
            embed.description = "Send anonymous messages safely."
            embed.add_field(name="Commands", value=(
                "**`/confess <user> <message>`**\n› Send an anonymous DM to a user.\n\n"
                "**`/confessions activate` / `deactivate`**\n› Choose whether you want to receive confessions.\n\n"
                "**`/confessions unblock_all`**\n› Unblock previously blocked senders."), inline=False)

        # Page 5: Utilities & Admin
        elif page_num == 4:
            embed.title = f"🛠️ {bot_name} Help: Utilities"
            embed.add_field(name="Tools", value=(
                f"**`/say <message>`**\n› Make me say something (Pings disabled).\n\n"
                f"**`/convert <link> <name>`**\n› Create a server emoji from a GIF or image link.\n\n"
                f"**`/ping`**\n› Check my connection speed."), inline=False)
            embed.add_field(name="⚙️ Admin", value=(
                f"**`/setprefix <prefix>`** / `{prefix}setprefix`\n› Change my command prefix for this server."), inline=False)

        embed.set_footer(text=f"Page {page_num + 1}/{total_pages} • Use the arrows to navigate.")
        return embed

    @commands.hybrid_command(name="help", description="Shows the bot's detailed, paginated help information.")
    async def help(self, ctx: commands.Context):
        is_interaction = ctx.interaction is not None
        if is_interaction: await ctx.defer(ephemeral=False)

        display_prefix = config.COMMAND_PREFIX
        if ctx.guild:
            display_prefix = self.bot.guild_prefixes.get(str(ctx.guild.id), config.COMMAND_PREFIX)

        # --- UPDATE TOTAL PAGES ---
        total_pages = 5
        current_page = 0
        initial_embed = self.get_help_page_content(current_page, total_pages, display_prefix)
        
        message = await ctx.send(embed=initial_embed)
        if not message and is_interaction:
            try: message = await ctx.interaction.original_response()
            except discord.NotFound: logger.error(f"Failed to get original response for help in guild {ctx.guild.id if ctx.guild else 'DM'}"); return

        if total_pages <= 1 or not message: return
        try:
            await message.add_reaction("◀️")
            await message.add_reaction("▶️")
        except discord.Forbidden: logger.warning(f"Missing 'Add Reactions' for help in guild {ctx.guild.id if ctx.guild else 'DM'}."); return
        
        def check(reaction, user):
            return user.id == ctx.author.id and reaction.message.id == message.id and str(reaction.emoji) in ["◀️", "▶️"]

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=HELP_TIMEOUT, check=check)
                valid_move = False
                if str(reaction.emoji) == "▶️" and current_page < total_pages - 1:
                    current_page += 1; valid_move = True
                elif str(reaction.emoji) == "◀️" and current_page > 0:
                    current_page -= 1; valid_move = True
                
                if valid_move:
                    await message.edit(embed=self.get_help_page_content(current_page, total_pages, display_prefix))
                
                if ctx.guild:
                    try: await message.remove_reaction(reaction.emoji, user)
                    except: pass
            except asyncio.TimeoutError:
                try:
                    await message.clear_reactions()
                    timeout_embed = message.embeds[0]
                    if timeout_embed:
                        timeout_embed.set_footer(text=f"Page {current_page + 1}/{total_pages} (Pagination timed out)")
                        await message.edit(embed=timeout_embed)
                except: pass
                break
            except Exception as e:
                logger.exception(f"Error during help pagination: {e}"); break
    
    def create_list_page_embed(self, page_num, total_pages, stream_keys):
        start_index = page_num * LIST_ITEMS_PER_PAGE; end_index = start_index + LIST_ITEMS_PER_PAGE
        keys_on_page = stream_keys[start_index:end_index]; display_prefix = config.COMMAND_PREFIX
        
        # User doesn't need to know the source (JSON vs PocketBase)
        embed = discord.Embed(title="📻 Radio Stations", description=f"Use `{display_prefix}play <ID>` or `{display_prefix}play <Name>`.\nYou can also search by partial name.", color=discord.Color.orange())
        
        if not keys_on_page: embed.add_field(name="Streams", value="*No streams on this page.*", inline=False)
        else:
            list_content = ""
            for i, key in enumerate(keys_on_page, start=start_index):
                stream_data = self.bot.station_manager.get_station(key)
                if not stream_data: continue
                description = stream_data.get("desc", "No description")
                # Clean up description presentation if needed
                list_content += f"**{i+1}.** `{key}`\n└ *{description}*\n"
            if len(list_content) > 1024: list_content = list_content[:1020] + "\n..."
            embed.add_field(name="Available Streams", value=list_content, inline=False)
        embed.set_footer(text=f"Page {page_num + 1}/{total_pages}"); return embed

    @commands.hybrid_command(name="list", description="Browses the list of available radio stations.")
    async def list(self, ctx: commands.Context):
        is_interaction = ctx.interaction is not None
        if is_interaction: await ctx.defer(ephemeral=False)
        
        # Refresh stations if using PocketBase to get latest updates
        if config.POCKETBASE_URL:
            await self.bot.station_manager.fetch_stations()
            
        stream_keys = list(self.bot.station_manager.stations.keys())
        if not stream_keys: await ctx.send("No streams configured.", ephemeral=True); return
        total_pages = math.ceil(len(stream_keys) / LIST_ITEMS_PER_PAGE); current_page = 0
        initial_embed = self.create_list_page_embed(current_page, total_pages, stream_keys)
        message = await ctx.send(embed=initial_embed)
        if is_interaction and not message:
            try: message = await ctx.interaction.original_response()
            except: await ctx.send("Failed pagination.", ephemeral=True); return
        if total_pages <= 1 or not message: return
        try: await message.add_reaction("◀️"); await message.add_reaction("▶️")
        except: return
        def check(reaction, user): return (user.id == ctx.author.id and reaction.message.id == message.id and str(reaction.emoji) in ["◀️", "▶️"])
        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=HELP_TIMEOUT, check=check)
                valid_move = False
                if str(reaction.emoji) == "▶️" and current_page < total_pages - 1: current_page += 1; valid_move = True
                elif str(reaction.emoji) == "◀️" and current_page > 0: current_page -= 1; valid_move = True
                if valid_move:
                    await message.edit(embed=self.create_list_page_embed(current_page, total_pages, stream_keys))
                if ctx.guild:
                    try: await message.remove_reaction(reaction.emoji, user)
                    except: pass
            except: break

async def setup(bot: RadioBot):
    await bot.add_cog(Utility(bot))