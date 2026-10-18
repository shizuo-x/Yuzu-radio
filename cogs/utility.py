# ./discord-radio-bot-modular/cogs/utility.py

import discord
from discord.ext import commands
import logging
import math
import random
import asyncio

import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.utility')

# Browse/list UI tuning (discord.py hard limits: 25 select options, 100-char labels/values)
LIST_STATIONS_PER_PAGE = 25   # station options shown per page in the browser select
LIST_SCOPE_OPTION_LIMIT = 24  # max options in the scope (filter) select
LIST_NAME_DISPLAY_LIMIT = 60  # max chars of a station name shown in the embed list
HELP_TOTAL_PAGES = 5
BROWSER_TIMEOUT = 180.0       # /list browser View timeout
HELP_TIMEOUT = 180.0          # /help paginator View timeout

_SCOPE_ALL = 'all'
_SCOPE_FEATURED = 'featured'


class HelpView(discord.ui.View):
    """Author-locked button paginator for /help (prev/next only)."""

    def __init__(self, cog: 'Utility', author_id: int, total_pages: int, prefix: str):
        super().__init__(timeout=HELP_TIMEOUT)
        self.cog = cog
        self.author_id = author_id
        self.total_pages = total_pages
        self.prefix = prefix
        self.page = 0
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who opened /help can navigate it.", ephemeral=True)
            return False
        return True

    def _current_embed(self) -> discord.Embed:
        return self.cog.get_help_page_content(self.page, self.total_pages, self.prefix)

    def _sync_buttons(self):
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    async def _show(self, interaction: discord.Interaction):
        self._sync_buttons()
        try:
            await interaction.response.edit_message(embed=self._current_embed(), view=self)
        except discord.HTTPException:
            logger.exception("Failed to edit help paginator message.")

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀", row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await self._show(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶", row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
        await self._show(interaction)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if not self.message:
            return
        try:
            embed = self.message.embeds[0] if self.message.embeds else None
            if embed and embed.footer and embed.footer.text:
                embed.set_footer(text=f"{embed.footer.text} (timed out)")
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException as e:
            logger.warning(f"Failed to mark help paginator as timed out: {e}")


class StationBrowserView(discord.ui.View):
    """discord.ui replacement for the old reaction-paginated /list.

    Row 0: scope (filter) select — All / Featured / top countries / top tags.
    Row 1: station select — current page (25/page) of the active scope; picking
           an option plays it via the shared playback pipeline.
    Row 2: Previous / Next / Shuffle / Cancel buttons.
    """

    def __init__(self, cog: 'Utility', entries: list, author_id: int):
        super().__init__(timeout=BROWSER_TIMEOUT)
        self.cog = cog
        self.author_id = author_id
        self.all_entries = entries  # snapshot: [(name, data)] alphabetical
        self.scope_key = _SCOPE_ALL
        self.scope_labels = {_SCOPE_ALL: 'All stations'}
        self.page = 0
        self.message = None

        self.scope_select = discord.ui.Select(
            custom_id='yuzu:list:scope', placeholder='Filter', row=0,
            options=self._build_scope_options())
        self.scope_select.callback = self._scope_callback
        self.add_item(self.scope_select)

        self.station_select = discord.ui.Select(
            custom_id='yuzu:list:station', placeholder='Pick a station to play', row=1,
            options=self._station_options(self.all_entries[:LIST_STATIONS_PER_PAGE]))
        self.station_select.callback = self._station_callback
        self.add_item(self.station_select)

    # --- scope helpers ---

    def _build_scope_options(self) -> list:
        """All + Featured + top countries by count + top tags by frequency (<=24)."""
        entries = self.all_entries
        options = [discord.SelectOption(
            label='All stations', value=_SCOPE_ALL,
            description=f'{len(entries)} stations'[:100], default=True)]
        featured = [(n, d) for n, d in entries if d.get('is_featured')]
        if featured:
            options.append(discord.SelectOption(
                label='Featured', value=_SCOPE_FEATURED,
                description=f'Hand-picked stations ({len(featured)})'[:100]))
            self.scope_labels[_SCOPE_FEATURED] = 'Featured'

        country_counts = {}
        tag_counts = {}
        for _, data in entries:
            country = (data.get('country') or '').strip()
            if country:
                country_counts[country] = country_counts.get(country, 0) + 1
            for tag in data.get('tags', []):
                tag = str(tag).strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        for country, count in sorted(country_counts.items(), key=lambda kv: (-kv[1], kv[0].casefold())):
            if len(options) >= LIST_SCOPE_OPTION_LIMIT:
                break
            value = f'country:{country}'
            options.append(discord.SelectOption(
                label=country[:100], value=value[:100],
                description=f'{count} stations'[:100]))
            self.scope_labels[value] = country[:100]
        for tag, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0].casefold())):
            if len(options) >= LIST_SCOPE_OPTION_LIMIT:
                break
            value = f'tag:{tag}'
            options.append(discord.SelectOption(
                label=f'#{tag}'[:100], value=value[:100],
                description=f'{count} stations'[:100]))
            self.scope_labels[value] = f'Tag: {tag}'[:100]
        return options

    def _scoped_entries(self) -> list:
        key = self.scope_key
        if key == _SCOPE_ALL:
            return self.all_entries
        if key == _SCOPE_FEATURED:
            return [(n, d) for n, d in self.all_entries if d.get('is_featured')]
        kind, _, arg = key.partition(':')
        if not arg:
            return self.all_entries
        arg_cf = arg.casefold()
        if kind == 'country':
            return [(n, d) for n, d in self.all_entries
                    if (d.get('country') or '').casefold() == arg_cf]
        if kind == 'tag':
            return [(n, d) for n, d in self.all_entries
                    if arg_cf in (t.casefold() for t in d.get('tags', []))]
        return self.all_entries

    # --- render helpers ---

    def _station_options(self, page_entries: list) -> list:
        if not page_entries:
            return [discord.SelectOption(
                label='No stations in this scope', value='__none__',
                description='Choose a different filter.'[:100])]
        options = []
        for name, data in page_entries:
            country = (data.get('country') or '').strip()
            tags = ', '.join(str(t) for t in data.get('tags', [])[:3])
            desc = ' · '.join(p for p in (country, tags) if p) or 'No metadata'
            options.append(discord.SelectOption(
                label=name[:100],
                description=desc[:100],
                value=(data.get('station_id') or name)[:100]))  # station_id value, name fallback
        return options

    def _build_embed(self, entries: list, page: int) -> discord.Embed:
        total = len(entries)
        total_pages = max(1, math.ceil(total / LIST_STATIONS_PER_PAGE))
        start = page * LIST_STATIONS_PER_PAGE
        page_entries = entries[start:start + LIST_STATIONS_PER_PAGE]

        lines = []
        for i, (name, data) in enumerate(page_entries, start=start + 1):
            display = name[:LIST_NAME_DISPLAY_LIMIT]
            country = (data.get('country') or '').strip()[:30]
            lines.append(f"{i}. **{display}**" + (f" · {country}" if country else ""))
        # ~25 lines * ~100 chars is well under the 4096-char description limit
        description = ("Pick a station below to play it instantly.\n\n"
                       + ("\n".join(lines) if lines else "*No stations in this scope.*"))

        embed = discord.Embed(
            title=f"📻 Radio Stations — {self.scope_labels.get(self.scope_key, 'All stations')}",
            description=description, color=discord.Color.orange())
        try:
            embed.set_thumbnail(url=self.cog.bot.user.display_avatar.url)
        except discord.HTTPException:
            pass
        end = start + len(page_entries)
        embed.set_footer(text=f"Page {page + 1}/{total_pages} - {start + 1}-{end} of {total}")
        return embed

    def _render(self) -> discord.Embed:
        entries = self._scoped_entries()
        total_pages = max(1, math.ceil(len(entries) / LIST_STATIONS_PER_PAGE))
        self.page = max(0, min(self.page, total_pages - 1))  # clamp page bounds
        start = self.page * LIST_STATIONS_PER_PAGE
        page_entries = entries[start:start + LIST_STATIONS_PER_PAGE]

        self.station_select.options = self._station_options(page_entries)
        self.station_select.disabled = not page_entries
        for option in self.scope_select.options:
            option.default = (option.value == self.scope_key)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= total_pages - 1
        self.shuffle_button.disabled = not entries
        return self._build_embed(entries, self.page)

    # --- callbacks ---

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who opened /list can browse it.", ephemeral=True)
            return False
        return True

    async def _scope_callback(self, interaction: discord.Interaction):
        self.scope_key = self.scope_select.values[0][:100]
        self.page = 0  # applying a scope resets to page 0
        await interaction.response.edit_message(embed=self._render(), view=self)

    async def _station_callback(self, interaction: discord.Interaction):
        value = self.station_select.values[0]
        station_name = None
        for name, data in self.all_entries:
            if value in ((data.get('station_id') or name), name):
                station_name = name
                break
        if station_name is None:
            await interaction.response.send_message(
                "That station is no longer available.", ephemeral=True)
            return
        await self.cog.play_selected_station(interaction, station_name)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀", row=2)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await interaction.response.edit_message(embed=self._render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶", row=2)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries = self._scoped_entries()
        total_pages = max(1, math.ceil(len(entries) / LIST_STATIONS_PER_PAGE))
        if self.page < total_pages - 1:
            self.page += 1
        await interaction.response.edit_message(embed=self._render(), view=self)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.primary, emoji="🔀", row=2)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries = self._scoped_entries()
        if not entries:
            await interaction.response.send_message(
                "No stations in this scope.", ephemeral=True)
            return
        station_name, _ = random.choice(entries)
        await self.cog.play_selected_station(interaction, station_name)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖", row=2)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if not self.message:
            return
        try:
            embed = self.message.embeds[0] if self.message.embeds else None
            if embed and embed.footer and embed.footer.text:
                embed.set_footer(text=f"{embed.footer.text} (timed out)")
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException as e:
            logger.warning(f"Failed to mark station browser as timed out: {e}")


class Utility(commands.Cog):
    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("Utility Cog initialized.")

    @commands.hybrid_command(name="ping", description="Checks the bot's latency.")
    async def ping(self, ctx: commands.Context):
        await ctx.send(f"Pong! Latency: {self.bot.latency * 1000:.2f} ms", ephemeral=True)

    # --- Shared play relay for the station browser ---

    async def play_selected_station(self, interaction: discord.Interaction, station_name: str):
        """Relays a station selection from the browser to the playback pipeline.

        Acks immediately (defer), validates the clicker's voice state, then
        relays the result text returned by _play_command_logic. The play
        pipeline itself (permissions, probing, verification) is owned by
        cogs/playback.py.
        """
        await interaction.response.defer(ephemeral=True)  # ack within the 3s window
        voice_state = getattr(interaction.user, 'voice', None)
        if not interaction.guild_id or voice_state is None or voice_state.channel is None:
            await interaction.followup.send("Join a voice channel first.", ephemeral=True)
            return

        playback_cog = self.bot.get_cog('Playback')
        if playback_cog is None:
            logger.error("Playback cog not found; cannot play station from browser.")
            await interaction.followup.send(
                "Playback is unavailable right now. Try `/play` instead.", ephemeral=True)
            return

        try:
            result = await playback_cog._play_command_logic(
                interaction.guild_id,
                interaction.user,          # the clicker's member
                interaction.channel_id,
                voice_state.channel,       # the clicker's voice channel
                station_name)
        except asyncio.TimeoutError:
            logger.exception(f"Timed out playing '{station_name}' from browser.")
            result = "Timed out while connecting. Please try again."
        except discord.HTTPException as e:
            logger.exception(f"Discord error playing '{station_name}' from browser: {e}")
            result = "A Discord error occurred while trying to play that station."
        except Exception as e:
            logger.exception(f"Unexpected error playing '{station_name}' from browser: {e}")
            result = "Something went wrong while trying to play that station."
        await interaction.followup.send(result, ephemeral=True)

    # --- FULLY REVISED HELP COMMAND ---
    # --- FULLY REVISED HELP COMMAND ---

    def get_help_page_content(self, page_num: int, total_pages: int, prefix: str) -> discord.Embed:
        """Creates the rich embed for a specific help page with detailed descriptions."""
        embed = discord.Embed(color=config.DEFAULT_EMBED_COLOR)
        try:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        except discord.HTTPException:
            pass

        # Determine the bot's name to use in examples
        bot_name = self.bot.user.name if self.bot.user else "Yuzu"
        
        # Determine the bot's name to use in examples
        bot_name = self.bot.user.name if self.bot.user else "Yuzu"

        # Page 1: Introduction & Radio
        if page_num == 0:
            embed.title = f"🎧 {bot_name} Help: Radio & Playback"
            embed.description = (f"Hi! I'm {bot_name}, your 24/7 radio companion.\n\n"
                                 f"**Prefix:** `{prefix}` or use Slash Commands (`/`).")
            embed.add_field(name="📻 Radio Commands", value=(
                f"**`/list`** / `{prefix}list`\n› **Browse & filter stations** by country or tag, then pick one from the menu to play it instantly.\n\n"
                f"**`/play <query>`** / `{prefix}play <query>`\n› Play a station by **Name** or **Number**.\n› *Example:* `/play 1` or `/play lofi`\n\n"
                f"**`/now`** / `{prefix}now`\n› See what's currently playing.\n\n"
                f"**`/stop`** / `{prefix}stop`\n› Stop playback.\n\n"
                f"**`/leave`** / `{prefix}dc`\n› Disconnect me from the voice channel."), inline=False)
            embed.add_field(name="▶️ Controls", value=f"React with {config.STOP_REACTION} on the player message to stop playback.", inline=False)
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
            embed.title = f"🤖 {bot_name} Help: AI Assistant"
            embed.description = f"Chat with me directly! Just mention me (`@Bot`) to start a conversation."
            embed.add_field(name="How to Use", value=(
                "I remember the last few messages, so you can ask follow-up questions!\n\n"
                f"**Example:** `@{bot_name} What is the capital of France?`\n"
                f"**Example:** `@{bot_name} Tell me a joke!`"
            ), inline=False)
            embed.add_field(name="⚠️ Privacy", value="Messages mentioned to me are sent to the AI provider (Google Gemini) for processing.", inline=False)

        # Page 3: Reminders
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

        embed.set_footer(text=f"Page {page_num + 1}/{total_pages} • Use the buttons to navigate.")
        return embed

    @commands.hybrid_command(name="help", description="Shows the bot's detailed, paginated help information.")
    async def help(self, ctx: commands.Context):
        is_interaction = ctx.interaction is not None
        if is_interaction: await ctx.defer(ephemeral=False)

        display_prefix = config.COMMAND_PREFIX
        if ctx.guild:
            display_prefix = self.bot.guild_prefixes.get(str(ctx.guild.id), config.COMMAND_PREFIX)

        view = HelpView(self, ctx.author.id, HELP_TOTAL_PAGES, display_prefix)
        initial_embed = self.get_help_page_content(view.page, HELP_TOTAL_PAGES, display_prefix)
        message = await ctx.send(embed=initial_embed, view=view)
        if not message and is_interaction:
            try:
                message = await ctx.interaction.original_response()
            except discord.NotFound:
                logger.error(f"Failed to get original response for help in guild {ctx.guild.id if ctx.guild else 'DM'}.")
                return
        view.message = message

    # --- STATION BROWSER (replaces the old reaction-paginated list) ---

    @commands.hybrid_command(name="list", description="Browses the list of available radio stations.")
    @commands.hybrid_command(name="list", description="Browses the list of available radio stations.")
    async def list(self, ctx: commands.Context):
        is_interaction = ctx.interaction is not None
        if is_interaction: await ctx.defer(ephemeral=True)  # ephemeral for slash, public for prefix

        # TTL refresh (no unconditional PocketBase fetch on the interaction path)
        try:
            await self.bot.station_manager.ensure_fresh(max_age_seconds=300)
        except Exception:
            logger.exception("Failed to refresh station cache for /list; using cached data.")

        entries = self.bot.station_manager.get_all_ordered()
        if not entries:
            await ctx.send("No streams configured.", ephemeral=True)
            return

        view = StationBrowserView(self, entries, ctx.author.id)
        embed = view._render()
        if is_interaction:
            message = await ctx.send(embed=embed, view=view, ephemeral=True)
        else:
            message = await ctx.send(embed=embed, view=view)
        if is_interaction and not message:
            try:
                message = await ctx.interaction.original_response()
            except discord.HTTPException:
                logger.error("Failed to send station browser message.")
                return
        view.message = message

async def setup(bot: RadioBot):
    await bot.add_cog(Utility(bot))
