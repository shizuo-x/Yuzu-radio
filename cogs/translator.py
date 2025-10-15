# ./discord-radio-bot-modular/cogs/translator.py

import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
import aiohttp
import aiosqlite # For the database
import datetime
import re # For parsing user IDs
from typing import Dict, Any, Optional, List

# Import configuration and the main bot class type hint
import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.translator')

# Cooldown for transparency messages
transparency_message_cooldowns: Dict[int, datetime.datetime] = {}
COOLDOWN_SECONDS = 3600 # 1 hour

class Translator(commands.Cog):
    """Cog for real-time, cross-server message translation with advanced filtering."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        self.translation_cache: Dict[int, List[aiosqlite.Row]] = {}
        self.bot.loop.create_task(self.setup_database())
        logger.info("Translator Cog initialized.")

    async def setup_database(self):
        """Creates the database and table with new fields if they don't exist."""
        async with aiosqlite.connect(config.TRANSLATIONS_DB_FILE) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_guild_id BIGINT NOT NULL,
                    source_channel_id BIGINT NOT NULL,
                    destination_type TEXT NOT NULL,
                    destination_id BIGINT NOT NULL,
                    target_language TEXT NOT NULL,
                    created_by_user_id BIGINT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    forced_source_language TEXT,
                    whitelisted_user_ids TEXT
                )
            """)
            await db.commit()
        logger.info(f"Database '{config.TRANSLATIONS_DB_FILE}' is ready.")
        await self.load_subscriptions_to_cache()

    async def load_subscriptions_to_cache(self):
        """Loads all subscriptions from the DB into the in-memory cache."""
        self.translation_cache.clear()
        async with aiosqlite.connect(config.TRANSLATIONS_DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM subscriptions") as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    source_channel_id = row['source_channel_id']
                    if source_channel_id not in self.translation_cache:
                        self.translation_cache[source_channel_id] = []
                    self.translation_cache[source_channel_id].append(row)
        logger.info(f"Loaded {len(rows)} translation subscription(s) into cache.")

    # (Core Translation Logic and Event Listener remain unchanged)
    async def translate_text(self, text: str, target_lang: str, source_lang: str = "auto") -> Optional[Dict[str, Any]]:
        if not text or not self.bot.http_session: return None
        url = f"{config.LIBRETRANSLATE_API_URL}/translate"
        payload = { "q": text, "source": source_lang, "target": target_lang, "format": "text" }
        try:
            async with self.bot.http_session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    detected_lang = data.get("detectedLanguage", {}).get("language")
                    if source_lang == "auto" and detected_lang == target_lang and data.get("detectedLanguage", {}).get("confidence", 0) > 90:
                        logger.debug(f"Skipping translation, source '{detected_lang}' matches target.")
                        return None
                    return data
                else:
                    logger.error(f"LibreTranslate API error ({response.status}): {await response.text()}"); return None
        except (asyncio.TimeoutError, aiohttp.ClientError) as e: logger.error(f"Network error calling LibreTranslate API: {e}"); return None
        except Exception as e: logger.exception(f"Unexpected error during translation: {e}"); return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content: return
        if message.channel.id not in self.translation_cache: return
        subscriptions = self.translation_cache.get(message.channel.id, [])
        for sub in subscriptions:
            whitelisted_ids_str = sub['whitelisted_user_ids']
            if whitelisted_ids_str:
                whitelisted_ids = [int(id_str) for id_str in whitelisted_ids_str.split(',')]
                if message.author.id not in whitelisted_ids:
                    continue
            asyncio.create_task(self.process_and_send_translation(message, sub))

    async def process_and_send_translation(self, message: discord.Message, subscription: aiosqlite.Row):
        target_lang = subscription['target_language']
        source_lang = subscription['forced_source_language'] or "auto"
        translation_data = await self.translate_text(message.content, target_lang, source_lang)
        if not translation_data or 'translatedText' not in translation_data: return
        translated_text = translation_data['translatedText']
        detected_lang_code = translation_data.get("detectedLanguage", {}).get("language", "N/A")
        embed = discord.Embed(description=translated_text, color=config.DEFAULT_EMBED_COLOR, timestamp=message.created_at)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.set_footer(text=f"Translated from {message.guild.name} | #{message.channel.name} (Source: {detected_lang_code})")
        dest_type = subscription['destination_type']
        dest_id = subscription['destination_id']
        destination = None
        try:
            logger.debug(f"Attempting to send translation for sub {subscription['id']} to {dest_type} {dest_id}")
            if dest_type == 'channel': destination = self.bot.get_channel(dest_id) or await self.bot.fetch_channel(dest_id)
            elif dest_type == 'dm': destination = self.bot.get_user(dest_id) or await self.bot.fetch_user(dest_id)
            if destination: await destination.send(embed=embed)
            else: logger.warning(f"Could not find destination {dest_type} ID {dest_id} for sub {subscription['id']}.")
        except discord.Forbidden: logger.error(f"Permissions Error: Cannot send to {dest_type} {dest_id} for sub {subscription['id']}.")
        except discord.NotFound: logger.warning(f"Not Found Error: Destination {dest_type} {dest_id} for sub {subscription['id']} no longer exists.")
        except Exception as e: logger.exception(f"Failed to send translated message for sub {subscription['id']}: {e}")

    # (Commands: translate_list and translate_unsubscribe remain unchanged)
    translate_group = app_commands.Group(name="translate", description="Commands for managing message translations.")
    @translate_group.command(name="list", description="Lists your active translation subscriptions.")
    async def translate_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        subscriptions = []
        async with aiosqlite.connect(config.TRANSLATIONS_DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM subscriptions WHERE created_by_user_id = ?", (interaction.user.id,)) as cursor:
                subscriptions = await cursor.fetchall()
        if not subscriptions: await interaction.followup.send("You have no active translation subscriptions.", ephemeral=True); return
        embed = discord.Embed(title="Your Translation Subscriptions", color=config.DEFAULT_EMBED_COLOR)
        description = ""
        for sub in subscriptions:
            source_ch = self.bot.get_channel(sub['source_channel_id'])
            source_text = f"**Source:** {source_ch.guild.name} > #{source_ch.name}" if source_ch else f"**Source:** Unknown Channel ({sub['source_channel_id']})"
            dest_text = "**Destination:** "
            if sub['destination_type'] == 'dm': dest_text += "Your DMs"
            else:
                dest_ch = self.bot.get_channel(sub['destination_id'])
                dest_text += f"{dest_ch.guild.name} > #{dest_ch.name}" if dest_ch else f"Unknown Channel ({sub['destination_id']})"
            description += f"**ID:** `{sub['id']}`\n{source_text}\n{dest_text}\n**To:** `{sub['target_language']}`"
            if sub['forced_source_language']: description += f"\n**Forcing Source:** `{sub['forced_source_language']}`"
            if sub['whitelisted_user_ids']:
                user_mentions = [f"<@{uid}>" for uid in sub['whitelisted_user_ids'].split(',')]
                description += f"\n**Translating Only:** {', '.join(user_mentions)}"
            description += "\n---\n"
        if len(description) > 4000: description = description[:4000] + "..."
        embed.description = description
        await interaction.followup.send(embed=embed, ephemeral=True)

    @translate_group.command(name="unsubscribe", description="Deletes translation subscriptions by ID.")
    @app_commands.describe(subscription_ids="Subscription ID(s) to delete (e.g., '5', '5,6,8', or 'all')")
    async def translate_unsubscribe(self, interaction: discord.Interaction, subscription_ids: str):
        await interaction.response.defer(ephemeral=True)
        ids_to_delete = []; delete_all = False
        input_str = subscription_ids.strip().lower()
        if input_str == 'all': delete_all = True
        else:
            try: ids_to_delete = [int(i.strip()) for i in input_str.split(',') if i.strip()]
            except ValueError: await interaction.followup.send("Error: Invalid format. Provide an ID, comma-separated IDs (e.g., `5,6,8`), or `all`.", ephemeral=True); return
        deleted_count = 0
        async with aiosqlite.connect(config.TRANSLATIONS_DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            if delete_all:
                cursor = await db.execute("DELETE FROM subscriptions WHERE created_by_user_id = ?", (interaction.user.id,)); deleted_count = cursor.rowcount
            else:
                for sub_id in ids_to_delete:
                    async with db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)) as cursor: sub = await cursor.fetchone()
                    if not sub: await interaction.followup.send(f"Warning: No subscription found with ID `{sub_id}`. Skipping.", ephemeral=True); continue
                    can_delete = False
                    if sub['created_by_user_id'] == interaction.user.id: can_delete = True
                    elif sub['destination_type'] == 'channel':
                        dest_channel = self.bot.get_channel(sub['destination_id'])
                        member = dest_channel.guild.get_member(interaction.user.id) if dest_channel and dest_channel.guild else None
                        if member and member.guild_permissions.manage_guild: can_delete = True
                    if can_delete: await db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,)); deleted_count += 1
                    else: await interaction.followup.send(f"Warning: You lack permission to delete subscription `{sub_id}`. Skipping.", ephemeral=True)
            await db.commit()
        await self.load_subscriptions_to_cache()
        logger.info(f"User {interaction.user} deleted {deleted_count} subscription(s).")
        await interaction.followup.send(f"✅ Successfully deleted {deleted_count} subscription(s).", ephemeral=True)

    # --- FIX: `subscribe_to_channel` command updated ---
    subscribe_group = app_commands.Group(name="subscribe", description="Subscribes to a channel for translation.", parent=translate_group)

    @subscribe_group.command(name="to_channel", description="Translate from a source channel to another channel.")
    @app_commands.describe(
        source_channel_id="The ID of the channel to translate FROM.",
        destination_channel_id="The ID of the channel to send translated messages TO.", # Changed
        target_language="2-letter language code to translate TO (e.g., en, es). Default: 'en'.",
        force_source_language="Optional: 2-letter language code to force translation FROM (e.g., tl).",
        translate_only_users="Optional: Mention or list user/bot IDs to translate, separated by commas."
    )
    # The decorator permission check is a basic guard; more specific check is done inside
    @app_commands.checks.has_permissions(manage_guild=True)
    async def subscribe_to_channel(self, interaction: discord.Interaction, source_channel_id: str, destination_channel_id: str, target_language: str = "en", force_source_language: Optional[str] = None, translate_only_users: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)

        # --- Manual Validation for both IDs ---
        try:
            source_channel_id_int = int(source_channel_id)
            destination_channel_id_int = int(destination_channel_id)
        except ValueError:
            await interaction.followup.send("Error: Both source and destination Channel IDs must be valid numbers.", ephemeral=True); return

        source_channel = self.bot.get_channel(source_channel_id_int)
        destination_channel = self.bot.get_channel(destination_channel_id_int)

        if not source_channel or not isinstance(source_channel, discord.TextChannel):
            await interaction.followup.send("Error: I cannot see the source channel. Ensure I'm in that server and the ID is correct.", ephemeral=True); return
        if not destination_channel or not isinstance(destination_channel, discord.TextChannel):
            await interaction.followup.send("Error: I cannot see the destination channel. Ensure I'm in that server and the ID is correct.", ephemeral=True); return

        # --- Critical Permission Check ---
        # Get the member object of the user in the DESTINATION guild
        dest_guild = destination_channel.guild
        member_in_dest = dest_guild.get_member(interaction.user.id)
        if not member_in_dest or not member_in_dest.guild_permissions.manage_guild:
            await interaction.followup.send(f"Error: You must have 'Manage Server' or 'Administrator' permissions in **{dest_guild.name}** to create this subscription.", ephemeral=True); return

        # --- Other Validation ---
        if source_channel.id == destination_channel.id:
            await interaction.followup.send("Error: Source and destination channels cannot be the same.", ephemeral=True); return
        if not destination_channel.permissions_for(dest_guild.me).send_messages:
            await interaction.followup.send(f"Error: I lack 'Send Messages' permission in the destination channel {destination_channel.mention}.", ephemeral=True); return

        # Process optional arguments
        whitelisted_user_ids = self._parse_user_ids(translate_only_users) if translate_only_users else []
        whitelisted_user_ids_str = ",".join(whitelisted_user_ids) if whitelisted_user_ids else None
        forced_source_lang_str = force_source_language.lower().strip() if force_source_language else None
        
        async with aiosqlite.connect(config.TRANSLATIONS_DB_FILE) as db:
            async with db.execute("SELECT 1 FROM subscriptions WHERE source_channel_id = ? AND destination_id = ? AND destination_type = ? AND target_language = ? AND IFNULL(forced_source_language, '') = ? AND IFNULL(whitelisted_user_ids, '') = ?", (source_channel.id, destination_channel.id, 'channel', target_language.lower(), forced_source_lang_str or '', whitelisted_user_ids_str or '')) as cursor:
                if await cursor.fetchone():
                    await interaction.followup.send("Error: This exact translation subscription already exists.", ephemeral=True); return
            await db.execute("INSERT INTO subscriptions (source_guild_id, source_channel_id, destination_type, destination_id, target_language, created_by_user_id, created_at, forced_source_language, whitelisted_user_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (source_channel.guild.id, source_channel.id, 'channel', destination_channel.id, target_language.lower(), interaction.user.id, datetime.datetime.now(datetime.timezone.utc), forced_source_lang_str, whitelisted_user_ids_str))
            await db.commit()
            
        await self.load_subscriptions_to_cache()
        logger.info(f"User {interaction.user} created channel subscription from {source_channel.id} to {destination_channel.id}.")
        await interaction.followup.send(f"✅ Success!\nI will now translate from `{source_channel.guild.name} > #{source_channel.name}` to {destination_channel.mention}.", ephemeral=True)

    # --- `subscribe_to_dm` command (unchanged but included for completeness) ---
    @subscribe_group.command(name="to_dm", description="Translate messages from a source channel to your DMs.")
    @app_commands.describe(
        source_channel_id="The ID of the channel to translate FROM.",
        target_language="2-letter language code (e.g., en, es). Default: 'en'.",
        force_source_language="Optional: 2-letter language code to force translation FROM (e.g., tl).",
        translate_only_users="Optional: Mention or list user/bot IDs to translate, separated by commas."
    )
    async def subscribe_to_dm(self, interaction: discord.Interaction, source_channel_id: str, target_language: str = "en", force_source_language: Optional[str] = None, translate_only_users: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        try: source_channel_id_int = int(source_channel_id)
        except ValueError: await interaction.followup.send("Error: Source Channel ID must be a valid number.", ephemeral=True); return
        source_channel = self.bot.get_channel(source_channel_id_int)
        if not source_channel or not isinstance(source_channel, discord.TextChannel): await interaction.followup.send("Error: I cannot see the source channel. Ensure I'm in that server and the ID is correct.", ephemeral=True); return
        whitelisted_user_ids = self._parse_user_ids(translate_only_users) if translate_only_users else []
        whitelisted_user_ids_str = ",".join(whitelisted_user_ids) if whitelisted_user_ids else None
        forced_source_lang_str = force_source_language.lower().strip() if force_source_language else None
        async with aiosqlite.connect(config.TRANSLATIONS_DB_FILE) as db:
            async with db.execute("SELECT 1 FROM subscriptions WHERE source_channel_id = ? AND destination_id = ? AND destination_type = ? AND target_language = ? AND IFNULL(forced_source_language, '') = ? AND IFNULL(whitelisted_user_ids, '') = ?", (source_channel.id, interaction.user.id, 'dm', target_language.lower(), forced_source_lang_str or '', whitelisted_user_ids_str or '')) as cursor:
                if await cursor.fetchone(): await interaction.followup.send("Error: You already have this exact DM subscription.", ephemeral=True); return
            await db.execute("INSERT INTO subscriptions (source_guild_id, source_channel_id, destination_type, destination_id, target_language, created_by_user_id, created_at, forced_source_language, whitelisted_user_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (source_channel.guild.id, source_channel.id, 'dm', interaction.user.id, target_language.lower(), interaction.user.id, datetime.datetime.now(datetime.timezone.utc), forced_source_lang_str, whitelisted_user_ids_str))
            await db.commit()
        await self.load_subscriptions_to_cache()
        logger.info(f"User {interaction.user} created DM subscription from {source_channel.id}.")
        await interaction.followup.send(f"✅ Success!\nI will now translate from `{source_channel.guild.name} > #{source_channel.name}` to your DMs.", ephemeral=True)

    def _parse_user_ids(self, user_input_str: str) -> List[str]:
        return re.findall(r'\d+', user_input_str)

# Setup function for discord.py to load the cog
async def setup(bot: RadioBot):
    await bot.add_cog(Translator(bot))