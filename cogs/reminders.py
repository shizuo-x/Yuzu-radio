# ./discord-radio-bot-modular/cogs/reminders.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import asyncio
import aiosqlite
import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError # For timezone handling
from typing import Optional, List

import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.reminders')

COMMON_TIMEZONES = [
    "UTC", "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
    "Europe/London", "Europe/Berlin", "Europe/Paris",
    "Asia/Tokyo", "Asia/Dubai", "Asia/Kolkata", "Australia/Sydney"
]

class Reminders(commands.Cog):
    """Cog for creating, managing, and sending persistent reminders."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        self.bot.loop.create_task(self.setup_database())
        self.check_reminders_loop.start()
        logger.info("Reminders Cog initialized.")
    
    def cog_unload(self):
        self.check_reminders_loop.cancel()
        logger.info("Reminders Cog unloaded.")

    async def setup_database(self):
        """Creates the necessary database tables if they don't exist."""
        # (This function is unchanged)
        async with aiosqlite.connect(config.REMINDERS_DB_FILE) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT NOT NULL, guild_id BIGINT, channel_id BIGINT NOT NULL, reminder_content TEXT NOT NULL, due_at TIMESTAMP NOT NULL, created_at TIMESTAMP NOT NULL, is_repeating BOOLEAN NOT NULL DEFAULT FALSE, repeat_interval INTEGER, timezone TEXT)
            """); await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_configs (guild_id BIGINT PRIMARY KEY, reminder_role_id BIGINT, reminder_channel_id BIGINT)
            """); await db.commit()
        logger.info(f"Database '{config.REMINDERS_DB_FILE}' is ready.")

    # --- UPDATED Main Task Loop ---
    @tasks.loop(seconds=config.REMINDER_CHECK_INTERVAL)
    async def check_reminders_loop(self):
        """Periodically checks the database for due reminders and processes them."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        logger.debug(f"Checking for reminders due at or before {now_utc.isoformat()}")
        try:
            async with aiosqlite.connect(config.REMINDERS_DB_FILE) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM reminders WHERE due_at <= ?", (now_utc,)) as cursor:
                    due_reminders = await cursor.fetchall()
                if not due_reminders: return
                logger.info(f"Found {len(due_reminders)} due reminder(s) to process.")
                
                for reminder in due_reminders:
                    try:
                        channel = self.bot.get_channel(reminder['channel_id']) or await self.bot.fetch_channel(reminder['channel_id'])
                        user = self.bot.get_user(reminder['user_id']) or await self.bot.fetch_user(reminder['user_id'])
                        
                        embed = discord.Embed(title="⏰ Reminder!", description=reminder['reminder_content'], color=discord.Color.gold(), timestamp=datetime.datetime.fromisoformat(reminder['created_at']))
                        
                        next_due_at = None
                        if reminder['is_repeating'] and reminder['repeat_interval']:
                            current_due = datetime.datetime.fromisoformat(reminder['due_at'])
                            # Calculate the next time based on the *last* due time to prevent drift
                            next_due_at = current_due + datetime.timedelta(seconds=reminder['repeat_interval'])
                            # If the bot was offline and the next_due_at is still in the past, fast-forward
                            while next_due_at < now_utc:
                                next_due_at += datetime.timedelta(seconds=reminder['repeat_interval'])

                            # --- FIX: Add "Next Reminder" info to the embed ---
                            embed.add_field(name="Next Reminder", value=f"<t:{int(next_due_at.timestamp())}:R>", inline=False)
                            embed.set_footer(text=f"This reminder repeats every {reminder['repeat_interval'] // 60} minutes.")
                        else:
                            embed.set_footer(text="This was a one-time reminder.")

                        await channel.send(content=user.mention, embed=embed)
                        logger.info(f"Sent reminder {reminder['id']} to user {user.id} in channel {channel.id}.")

                        if next_due_at: # If we calculated a next time
                            await db.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (next_due_at, reminder['id']))
                            logger.info(f"Rescheduled repeating reminder {reminder['id']} for {next_due_at.isoformat()}")
                        else:
                            await db.execute("DELETE FROM reminders WHERE id = ?", (reminder['id'],))
                            logger.info(f"Deleted one-time reminder {reminder['id']}.")

                    except (discord.NotFound, discord.Forbidden) as e:
                        logger.warning(f"Could not send reminder {reminder['id']}. Deleting. Error: {e}")
                        await db.execute("DELETE FROM reminders WHERE id = ?", (reminder['id'],))
                    except Exception as e:
                        logger.exception(f"Unexpected error processing reminder {reminder['id']}. Deleting.")
                        await db.execute("DELETE FROM reminders WHERE id = ?", (reminder['id'],))

                await db.commit()
        except Exception as e:
            logger.exception(f"A critical error occurred in the reminder checking loop: {e}")

    @check_reminders_loop.before_loop
    async def before_check_reminders(self):
        await self.bot.wait_until_ready()

    # --- UPDATED User Command ---
    reminders_group = app_commands.Group(name="reminders", description="Manage your personal reminders.")

    @app_commands.command(name="remind", description="Set a reminder for yourself.")
    @app_commands.describe(message="What do you want to be reminded of?", time="Time in 24h format (e.g., 13:45, 09:00).", date="Date in YYYY-MM-DD format (e.g., 2025-12-25).", timezone="Your timezone. Defaults to UTC.", repeat="Should this reminder repeat?", repeat_interval_minutes="How often to repeat, in minutes (if repeating).")
    @app_commands.choices(timezone=[app_commands.Choice(name=tz, value=tz) for tz in COMMON_TIMEZONES])
    @app_commands.choices(repeat=[app_commands.Choice(name="Yes, make it repeat", value=1), app_commands.Choice(name="No, just one time", value=0)])
    async def remind(self, interaction: discord.Interaction, message: str, time: str, date: str, timezone: app_commands.Choice[str] = None, repeat: app_commands.Choice[int] = None, repeat_interval_minutes: Optional[app_commands.Range[int, 1]] = None):
        # Permission Check (Unchanged)
        if interaction.guild:
            async with aiosqlite.connect(config.REMINDERS_DB_FILE) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM guild_configs WHERE guild_id = ?", (interaction.guild.id,)) as cursor: guild_config = await cursor.fetchone()
            is_admin = interaction.user.guild_permissions.administrator
            has_role = False
            if guild_config and guild_config['reminder_role_id']:
                try: role = interaction.guild.get_role(guild_config['reminder_role_id'])
                except: role = None
                if role and role in interaction.user.roles: has_role = True
            is_in_channel = guild_config and guild_config['reminder_channel_id'] == interaction.channel.id
            if not (is_admin or has_role or is_in_channel):
                await interaction.response.send_message("❌ You don't have permission to set reminders here.", ephemeral=True); return
        
        await interaction.response.defer(ephemeral=True)

        # Input Validation and Parsing (Unchanged)
        try:
            dt_str = f"{date} {time}"; user_dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            tz_str = timezone.value if timezone else "UTC"; user_tz = ZoneInfo(tz_str)
            aware_dt = user_dt.replace(tzinfo=user_tz); due_at_utc = aware_dt.astimezone(datetime.timezone.utc)
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if due_at_utc < now_utc: await interaction.followup.send("❌ Cannot set a reminder in the past.", ephemeral=True); return
        except (ValueError, TypeError): await interaction.followup.send("❌ Invalid date/time format. Use `YYYY-MM-DD` and `HH:MM`.", ephemeral=True); return
        except ZoneInfoNotFoundError: await interaction.followup.send("❌ Invalid timezone.", ephemeral=True); return
        
        is_repeating = bool(repeat.value) if repeat else False
        repeat_interval_sec = None
        if is_repeating:
            if not repeat_interval_minutes: await interaction.followup.send("❌ Repeating reminders need a `repeat_interval_minutes` value.", ephemeral=True); return
            repeat_interval_sec = repeat_interval_minutes * 60

        # --- Database Insertion (Unchanged) ---
        try:
            async with aiosqlite.connect(config.REMINDERS_DB_FILE) as db:
                await db.execute("INSERT INTO reminders (user_id, guild_id, channel_id, reminder_content, due_at, created_at, is_repeating, repeat_interval, timezone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel.id, message, due_at_utc, now_utc, is_repeating, repeat_interval_sec, tz_str))
                await db.commit()

            # --- FIX: New, more informative confirmation embed ---
            embed = discord.Embed(title="✅ Reminder Set!", color=discord.Color.green())
            embed.add_field(name="Message", value=f"```{message}```", inline=False)
            embed.add_field(name="Time", value=f"<t:{int(due_at_utc.timestamp())}:F> (<t:{int(due_at_utc.timestamp())}:R>)", inline=False)
            
            if is_repeating:
                embed.add_field(name="Repeats", value=f"Yes, every {repeat_interval_minutes} minutes.", inline=True)
            else:
                embed.add_field(name="Repeats", value="No, this is a one-time reminder.", inline=True)
            
            embed.set_footer(text=f"Timezone set to {tz_str}. Use /reminders list to see or delete your reminders.")

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"User {interaction.user.id} set a reminder for {due_at_utc.isoformat()}.")
        except Exception as e:
            logger.exception("Failed to save reminder to database.")
            await interaction.followup.send(f"❌ An error occurred saving your reminder: {e}", ephemeral=True)

    # (reminders_list, reminders_delete, and admin commands are unchanged)
    @reminders_group.command(name="list", description="Lists your upcoming reminders.")
    async def reminders_list(self, interaction: discord.Interaction):
        # ...
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(config.REMINDERS_DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM reminders WHERE user_id = ? ORDER BY due_at ASC", (interaction.user.id,)) as cursor: reminders = await cursor.fetchall()
        if not reminders: await interaction.followup.send("You have no upcoming reminders.", ephemeral=True); return
        embed = discord.Embed(title="Your Upcoming Reminders", color=config.DEFAULT_EMBED_COLOR)
        description = ""
        for rem in reminders:
            due_ts = int(datetime.datetime.fromisoformat(rem['due_at']).timestamp())
            repeat_text = f" (repeats every {rem['repeat_interval'] // 60} mins)" if rem['is_repeating'] else ""
            description += f"**ID:** `{rem['id']}` - <t:{due_ts}:f> (<t:{due_ts}:R>){repeat_text}\n› *{rem['reminder_content'][:100]}...*\n\n"
        embed.description = description[:4096]
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    @reminders_group.command(name="delete", description="Deletes an upcoming reminder.")
    @app_commands.describe(reminder_id="The ID of the reminder to delete (from /reminders list).")
    async def reminders_delete(self, interaction: discord.Interaction, reminder_id: int):
        # ...
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(config.REMINDERS_DB_FILE) as db:
            async with db.execute("SELECT 1 FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, interaction.user.id)) as cursor:
                if not await cursor.fetchone(): await interaction.followup.send("❌ Reminder not found or you don't own it.", ephemeral=True); return
            await db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)); await db.commit()
        await interaction.followup.send(f"✅ Reminder with ID `{reminder_id}` has been deleted.", ephemeral=True)

    reminders_admin_group = app_commands.Group(name="reminders_admin", description="Admin commands for configuring reminders.", default_permissions=discord.Permissions(administrator=True))
    @reminders_admin_group.command(name="set_role", description="[Admin] Set a role that is allowed to use the /remind command.")
    @app_commands.describe(role="The role to allow. Select None to clear.")
    async def set_reminder_role(self, interaction: discord.Interaction, role: Optional[discord.Role]):
        # ...
        if not interaction.guild: await interaction.response.send_message("This can only be used in a server.", ephemeral=True); return
        async with aiosqlite.connect(config.REMINDERS_DB_FILE) as db:
            await db.execute("INSERT OR REPLACE INTO guild_configs (guild_id, reminder_role_id) VALUES (?, ?)", (interaction.guild.id, role.id if role else None)); await db.commit()
        if role: await interaction.response.send_message(f"✅ The {role.mention} role can now use `/remind`.", ephemeral=True)
        else: await interaction.response.send_message(f"✅ The reminder role has been cleared.", ephemeral=True)

    @reminders_admin_group.command(name="set_channel", description="[Admin] Set a channel where anyone can use the /remind command.")
    @app_commands.describe(channel="The channel to whitelist. Select None to clear.")
    async def set_reminder_channel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel]):
        # ...
        if not interaction.guild: await interaction.response.send_message("This can only be used in a server.", ephemeral=True); return
        async with aiosqlite.connect(config.REMINDERS_DB_FILE) as db:
            await db.execute("INSERT OR REPLACE INTO guild_configs (guild_id, reminder_channel_id) VALUES (?, ?)", (interaction.guild.id, channel.id if channel else None)); await db.commit()
        if channel: await interaction.response.send_message(f"✅ Anyone can now use `/remind` in {channel.mention}.", ephemeral=True)
        else: await interaction.response.send_message(f"✅ The whitelisted reminder channel has been cleared.", ephemeral=True)

async def setup(bot: RadioBot):
    await bot.add_cog(Reminders(bot))