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

    # --- COMPLETE HELP COMMAND OVERHAUL ---

    def get_help_page_content(self, page_num: int, total_pages: int, prefix: str) -> discord.Embed:
        """Creates the rich embed for a specific help page with detailed descriptions."""
        embed = discord.Embed(color=config.DEFAULT_EMBED_COLOR)
        try:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        except:
            pass

        # Page 1: Introduction & Radio
        if page_num == 0:
            embed.title = "🎧 Yuzu Help: Radio & Playback"
            embed.description = (
                f"Welcome! I'm a multi-purpose bot with a focus on 24/7 radio, translation, and more.\n\n"
                f"**Your prefix in this context is `{prefix}`.** You can also use Slash Commands (`/`)."
            )
            embed.add_field(
                name="📻 Radio Commands",
                value=(
                    f"**`/play`** / `{prefix}play <Name>`\n"
                    f"› Starts playing a radio station. Use a predefined name from the list or a direct URL.\n\n"
                    f"**`/stop`** / `{prefix}stop`\n"
                    f"› Stops the music and clears the player.\n\n"
                    f"**`/leave`** / `{prefix}dc`\n"
                    f"› Disconnects the bot from the voice channel.\n\n"
                    f"**`/now`** / `{prefix}now`\n"
                    f"› Shows the 'Now Playing' info again, including song metadata if available."
                ),
                inline=False
            )

        # Page 2: Translation
        elif page_num == 1:
            embed.title = "🌐 Yuzu Help: Translation"
            embed.description = "Translate messages automatically between channels, servers, or to your DMs."
            embed.add_field(
                name="Subscription Commands (Slash Only)",
                value=(
                    "**`/translate_subscribe to_channel`**\n"
                    "› Translates messages *from* a `source_channel_id` *to* a `destination_channel_id`. Requires 'Manage Server' permission in the destination server.\n\n"
                    "**`/translate_subscribe to_dm`**\n"
                    "› Translates messages from a `source_channel_id` directly to your DMs.\n\n"
                    "**Optional Arguments for Subscribing:**\n"
                    "• `target_language`: Set the language to translate to (e.g., `es`, `ja`). Defaults to `en`.\n"
                    "• `force_source_language`: Force the bot to assume the source is a specific language (e.g., `tl` for Tagalog)."
                ),
                inline=False
            )
            embed.add_field(
                name="Management",
                value=(
                    "**`/translate_list`**\n"
                    "› Privately lists all of your active translation subscriptions and their IDs.\n\n"
                    "**`/translate_unsubscribe`**\n"
                    "› Deletes subscriptions. You can provide one ID, a comma-separated list (`5,8`), or `all`."
                ),
                inline=False
            )

        # Page 3: Confessions
        elif page_num == 2:
            embed.title = "💌 Yuzu Help: Anonymous Confessions"
            embed.description = "Send and receive anonymous direct messages with other users who share a server with the bot."
            embed.add_field(
                name="Sending & Receiving",
                value=(
                    "**`/confess <user> <message>`**\n"
                    "› Sends a private, anonymous message to a user. For `<user>`, you can use their User ID, @Mention, or unique username.\n\n"
                    "**Replying & Blocking**\n"
                    "› When you receive a confession, it will have buttons to `Reply`, `Block Sender`, or `Stop All Confessions`."
                ),
                inline=False
            )
            embed.add_field(
                name="Managing Your Privacy",
                value=(
                    "**`/confessions activate`**\n"
                    "› Allows you to start receiving confessions.\n\n"
                    "**`/confessions deactivate`**\n"
                    "› Stops you from receiving any new confessions.\n\n"
                    "**`/confessions unblock_all`**\n"
                    "› Removes all blocks you have placed on anonymous senders."
                ),
                inline=False
            )
            
        # Page 4: Utilities & Admin (MODIFIED)
        elif page_num == 3:
            embed.title = "🛠️ Yuzu Help: Utilities & Admin"
            embed.add_field(
                name="General Utilities",
                value=(
                    # --- ADDED /say command here ---
                    "**`/say <message>`**\n"
                    "› Makes the bot send the given message in the current channel. All pings are disabled for safety.\n\n"
                    # --- Existing commands below ---
                    f"**`/list`** / `{prefix}list`\n"
                    f"› Shows a paginated list of all predefined radio stations.\n\n"
                    f"**`/convert <link> <name>`**\n"
                    f"› Converts a GIF from a URL into a high-quality server emoji (requires 'Manage Expressions' permission).\n\n"
                    f"**`/ping`**\n"
                    f"› Checks the bot's responsiveness."
                ),
                inline=False
            )
            embed.add_field(
                name="⚙️ Admin Commands",
                value=(
                    f"**`/setprefix <prefix>`** / `{prefix}setprefix <prefix>`\n"
                    f"› (Admin Only) Changes the command prefix for this server. Use `reset` to go back to default."
                ),
                inline=False
            )

        # This page was showing "Playback Control", but let's remove it to keep the page count at 4
        # and because it's mentioned with the stop command implicitly.
        # This makes the help menu cleaner.

        embed.set_footer(text=f"Page {page_num + 1}/{total_pages} • Use the arrows to navigate.")
        return embed

    @commands.hybrid_command(name="help", description="Shows the bot's detailed, paginated help information.")
    async def help(self, ctx: commands.Context):
        is_interaction = ctx.interaction is not None
        if is_interaction: await ctx.defer(ephemeral=False)

        display_prefix = config.COMMAND_PREFIX
        if ctx.guild:
            display_prefix = self.bot.guild_prefixes.get(str(ctx.guild.id), config.COMMAND_PREFIX)

        # --- Adjusted total pages back to 4 ---
        total_pages = 4
        current_page = 0
        initial_embed = self.get_help_page_content(current_page, total_pages, display_prefix)
        
        message = await ctx.send(embed=initial_embed)
        if not message and is_interaction:
            try:
                message = await ctx.interaction.original_response()
            except discord.NotFound:
                logger.error(f"Failed to get original response for help in {ctx.guild.id if ctx.guild else 'DM'}")
                await ctx.send("Error: Could not start pagination.", ephemeral=True); return

        if total_pages <= 1 or not message: return
        try:
            await message.add_reaction("◀️")
            await message.add_reaction("▶️")
        except discord.Forbidden:
            logger.warning(f"Missing 'Add Reactions' for help in {ctx.guild.id if ctx.guild else 'DM'}."); return
        
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
    
    # (The `list` command and its helper remain unchanged and are correct)
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