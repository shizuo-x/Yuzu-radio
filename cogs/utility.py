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
            embed.description = (f"Welcome! I'm a multi-purpose bot with a focus on 24/7 radio and more.\n\n"
                                 f"**Your prefix is `{prefix}`.** You can also use Slash Commands (`/`).")
            embed.add_field(name="📻 Radio Commands", value=(
                f"**`/play`** / `{prefix}play <Name>`\n› Starts playing a radio station from the list or a URL.\n\n"
                f"**`/stop`** / `{prefix}stop`\n› Stops the music and clears the player.\n\n"
                f"**`/leave`** / `{prefix}dc`\n› Disconnects the bot from the voice channel.\n\n"
                f"**`/now`** / `{prefix}now`\n› Shows the 'Now Playing' info again."), inline=False)
            embed.add_field(name="▶️ Playback Control", value=f"React with {config.STOP_REACTION} on the 'Now Playing' message to stop playback.", inline=False)

        # Page 2: AI Assistant
        elif page_num == 1:
            embed.title = f"🤖 {bot_name} Help: AI Assistant"
            embed.description = f"You can talk to me directly by mentioning me at the start of your message!"
            embed.add_field(name="How to Use", value=(
                "Simply ping me and ask a question. I can remember the last few messages in our conversation within a channel, so you can ask follow-up questions!\n\n"
                f"**Example:** `@{bot_name} Hello, how are you today?`\n"
                f"**Example:** `@{bot_name} Can you tell me a fun fact about space?`"
            ), inline=False)
            embed.add_field(name="⚠️ Privacy Note", value="To provide conversational context, message content is sent to the Google Gemini API.", inline=False)

        # Page 3: Reminders
        elif page_num == 2:
            embed.title = f"⏰ {bot_name} Help: Reminders"
            embed.description = "Set personal or channel-wide reminders. All commands are Slash Commands only for the best user experience."
            embed.add_field(name="User Commands", value=(
                "**`/remind <message> <time> <date> [options]`**\n"
                "› Sets a reminder. You can specify a timezone, and set it to repeat at a certain interval in minutes.\n\n"
                "**`/reminders list`**\n"
                "› Privately lists all your upcoming reminders and their IDs.\n\n"
                "**`/reminders delete <id>`**\n"
                "› Deletes one of your reminders by its ID."
            ), inline=False)
            embed.add_field(name="Admin Setup", value=(
                "**`/reminders_admin set_role <role>`**\n"
                "› (Admin Only) Sets a role that can use `/remind` anywhere.\n\n"
                "**`/reminders_admin set_channel <channel>`**\n"
                "› (Admin Only) Sets a channel where anyone can use `/remind`."
            ), inline=False)
            
        # Page 4: Translation
        elif page_num == 3:
            embed.title = f"🌐 {bot_name} Help: Translation"
            embed.description = "Translate messages automatically between channels, servers, or to your DMs."
            embed.add_field(name="Subscription Commands (Slash Only)", value=(
                "**`/translate_subscribe to_channel`**\n› Translates from a `source_channel_id` to a `destination_channel_id`.\n\n"
                "**`/translate_subscribe to_dm`**\n› Translates from a `source_channel_id` to your DMs.\n\n"
                "**Optional Arguments:** `target_language`, `force_source_language`."), inline=False)
            embed.add_field(name="Management", value=("**`/translate_list`**\n› Privately lists your subscriptions.\n\n" "**`/translate_unsubscribe`**\n› Deletes subscriptions by ID (e.g., `5`, `5,8`, or `all`)."), inline=False)

        # Page 5: Confessions
        elif page_num == 4:
            embed.title = f"💌 {bot_name} Help: Anonymous Confessions"
            embed.description = "Send and receive anonymous direct messages."
            embed.add_field(name="Commands", value=(
                "**`/confess <user> <message>`**\n› Sends a private, anonymous message to a user.\n\n"
                "**`/confessions activate` / `deactivate`**\n› Toggles your ability to receive confessions.\n\n"
                "**`/confessions unblock_all`**\n› Removes all blocks you have placed."), inline=False)

        # Page 6: Utilities & Admin
        elif page_num == 5:
            embed.title = f"🛠️ {bot_name} Help: Utilities & Admin"
            embed.add_field(name="General Utilities", value=(
                f"**`/say <message>`**\n› Makes the bot send a message. Pings are disabled.\n\n"
                f"**`/list`** / `{prefix}list`\n› Shows the list of predefined radio stations.\n\n"
                f"**`/convert <link> <name>`**\n› Converts a GIF into a server emoji.\n\n"
                f"**`/ping`**\n› Checks the bot's responsiveness."), inline=False)
            embed.add_field(name="⚙️ Admin Commands", value=(
                f"**`/setprefix <prefix>`** / `{prefix}setprefix <prefix>`\n› (Admin Only) Changes the prefix for this server."), inline=False)

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
        total_pages = 6
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
    
    # (The `list` command and its helper are unchanged)
    def create_list_page_embed(self, page_num, total_pages, stream_keys):
        start_index = page_num * LIST_ITEMS_PER_PAGE; end_index = start_index + LIST_ITEMS_PER_PAGE
        keys_on_page = stream_keys[start_index:end_index]; display_prefix = config.COMMAND_PREFIX
        embed = discord.Embed(title="📻 Predefined Radio Streams", description=f"Use `{display_prefix}play <Name>` or `/play stream:<Name>`:", color=discord.Color.orange())
        if not keys_on_page: embed.add_field(name="Streams", value="*No streams on this page.*", inline=False)
        else:
            list_content = ""
            for i, key in enumerate(keys_on_page, start=start_index):
                stream_data = config.PREDEFINED_STREAMS.get(key, {}); description = stream_data.get("desc", "No description")
                list_content += f"**{i+1}.** `{key}` - *{description}*\n"
            if len(list_content) > 1024: list_content = list_content[:1020] + "\n..."
            embed.add_field(name="Available Streams", value=list_content, inline=False)
        embed.set_footer(text=f"Page {page_num + 1}/{total_pages}"); return embed

    @commands.hybrid_command(name="list", description="Shows the list of predefined radio streams.")
    async def list(self, ctx: commands.Context):
        is_interaction = ctx.interaction is not None
        if is_interaction: await ctx.defer(ephemeral=False)
        stream_keys = list(config.PREDEFINED_STREAMS.keys())
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