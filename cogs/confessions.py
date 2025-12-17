# ./discord-radio-bot-modular/cogs/confessions.py

import discord
from discord.ext import commands
from discord import app_commands
import logging
import aiosqlite
import datetime
import re
from typing import Optional

import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.confessions')

# --- UI Components (Unchanged) ---
class ReplyModal(discord.ui.Modal, title="Send a Reply"):
    reply_text = discord.ui.TextInput(label="Your Reply", style=discord.TextStyle.paragraph, placeholder="Type your reply here...", required=True, max_length=1500)
    def __init__(self, bot: RadioBot, confession_id: int):
        super().__init__(); self.bot = bot; self.confession_id = confession_id
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM confessions WHERE id = ?", (self.confession_id,)) as cursor: confession_data = await cursor.fetchone()
            if not confession_data: await interaction.followup.send("❌ Error: Could not find original confession.", ephemeral=True); return
            confessor_id = confession_data['confessor_id']; original_message = confession_data['message_content']
            async with db.execute("SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?", (confessor_id, interaction.user.id)) as cursor:
                if await cursor.fetchone(): await interaction.followup.send("❌ This user is unavailable.", ephemeral=True); return
        try: confessor = await self.bot.fetch_user(confessor_id)
        except discord.NotFound: await interaction.followup.send("❌ Error: Could not find the original sender.", ephemeral=True); return
        reply_embed = discord.Embed(title="💌 A Reply to Your Confession", color=discord.Color.green(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        reply_embed.add_field(name="Your Original Message:", value=f">>> {original_message}", inline=False)
        reply_embed.add_field(name="Their Reply:", value=f">>> {self.reply_text.value}", inline=False)
        try:
            await confessor.send(embed=reply_embed)
            await interaction.followup.send("✅ Your reply has been sent!", ephemeral=True)
        except discord.Forbidden: await interaction.followup.send("❌ Could not deliver reply. Sender may have DMs closed or blocked me.", ephemeral=True)

class ConfessionView(discord.ui.View):
    def __init__(self, bot: RadioBot, confession_id: int):
        super().__init__(timeout=None); self.bot = bot
        self.stop_all_button.custom_id = f"confession_deactivate_all:{confession_id}"
        self.block_sender_button.custom_id = f"confession_block_sender:{confession_id}"
        self.reply_button.custom_id = f"confession_reply:{confession_id}"
    @discord.ui.button(label="Stop All Confessions", style=discord.ButtonStyle.grey, emoji="🔕")
    async def stop_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db: await db.execute("INSERT OR REPLACE INTO users (user_id, can_receive, has_been_prompted) VALUES (?, ?, ?)", (interaction.user.id, False, True)); await db.commit()
        await interaction.response.send_message("You will no longer receive new confessions.", ephemeral=True)
    @discord.ui.button(label="Block Sender", style=discord.ButtonStyle.danger, emoji="🚫")
    async def block_sender_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try: confession_id = int(button.custom_id.split(":")[1])
        except (IndexError, ValueError): await interaction.followup.send("Error: Could not identify this confession.", ephemeral=True); return
        async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT confessor_id FROM confessions WHERE id = ?", (confession_id,)) as cursor: confession_data = await cursor.fetchone()
            if not confession_data: await interaction.followup.send("Error: Could not find original confession data.", ephemeral=True); return
            confessor_id = confession_data['confessor_id']
            async with db.execute("SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?", (interaction.user.id, confessor_id)) as cursor:
                if await cursor.fetchone(): await interaction.followup.send("You have already blocked this sender.", ephemeral=True); return
            await db.execute("INSERT INTO blocks (blocker_id, blocked_id) VALUES (?, ?)", (interaction.user.id, confessor_id)); await db.commit()
        await interaction.followup.send("This sender has been blocked.", ephemeral=True)
    @discord.ui.button(label="Reply", style=discord.ButtonStyle.primary, emoji="💬")
    async def reply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try: confession_id = int(button.custom_id.split(":")[1])
        except (IndexError, ValueError): await interaction.response.send_message("Error: Could not identify this confession.", ephemeral=True); return
        await interaction.response.send_modal(ReplyModal(self.bot, confession_id))

class ApprovalView(discord.ui.View):
    def __init__(self, bot: RadioBot):
        super().__init__(timeout=86400.0); self.bot = bot; self.message: Optional[discord.Message] = None
    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children: item.disabled = True
                embed = self.message.embeds[0]; embed.title = "⌛ Request Expired"; embed.color = discord.Color.greyple()
                await self.message.edit(embed=embed, view=self)
            except: pass
    async def _handle_response(self, interaction: discord.Interaction, accepted: bool):
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("INSERT OR REPLACE INTO users (user_id, can_receive, has_been_prompted) VALUES (?, ?, ?)", (interaction.user.id, accepted, True))
            async with db.execute("SELECT * FROM confessions WHERE target_id = ? AND status = ? ORDER BY id DESC LIMIT 1", (interaction.user.id, 'pending_approval')) as cursor: confession_data = await cursor.fetchone()
            if not confession_data: await interaction.followup.send("Could not find pending confession.", ephemeral=True); return
            try: confessor = await self.bot.fetch_user(confession_data['confessor_id'])
            except discord.NotFound: confessor = None
            if accepted:
                await db.execute("UPDATE confessions SET status = ? WHERE id = ?", ('sent', confession_data['id']))
                embed = discord.Embed(title="💌 Anonymous Confession", description=f">>> {confession_data['message_content']}", color=discord.Color.pink(), timestamp=datetime.datetime.fromisoformat(confession_data['timestamp']))
                await interaction.user.send(embed=embed, view=ConfessionView(self.bot, confession_data['id']))
                if confessor:
                    try: await confessor.send(f"✅ Your confession to **{interaction.user}** was delivered!")
                    except discord.Forbidden: pass
                new_embed = interaction.message.embeds[0]; new_embed.title = "✅ Accepted!"; new_embed.color = discord.Color.green()
                await interaction.edit_original_response(embed=new_embed, view=None)
            else:
                await db.execute("UPDATE confessions SET status = ? WHERE id = ?", ('denied', confession_data['id']))
                if confessor:
                    try: await confessor.send(f"❌ Your confession to **{interaction.user}** was declined.")
                    except discord.Forbidden: pass
                new_embed = interaction.message.embeds[0]; new_embed.title = "❌ Declined."; new_embed.description = "Your preference is saved."; new_embed.color = discord.Color.red()
                await interaction.edit_original_response(embed=new_embed, view=None)
            await db.commit()
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button): await self._handle_response(interaction, accepted=True)
    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button): await self._handle_response(interaction, accepted=False)
    
class UnblockAllView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60.0); self.confirmed = None
    @discord.ui.button(label="Yes, unblock all", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True; self.stop()
        for item in self.children: item.disabled = True; await interaction.response.edit_message(view=self)
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False; self.stop()
        for item in self.children: item.disabled = True; await interaction.response.edit_message(view=self)

class Confessions(commands.Cog):
    """Anonymous confession commands."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        self.bot.add_view(ConfessionView(bot, confession_id=0))
        self.bot.loop.create_task(self.setup_database())
        logger.info("Confessions Cog initialized.")

    async def setup_database(self):
        async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, can_receive BOOLEAN NOT NULL, has_been_prompted BOOLEAN NOT NULL)")
            await db.execute("CREATE TABLE IF NOT EXISTS confessions (id INTEGER PRIMARY KEY AUTOINCREMENT, confessor_id BIGINT NOT NULL, target_id BIGINT NOT NULL, confessor_name TEXT NOT NULL, target_name TEXT NOT NULL, message_content TEXT NOT NULL, timestamp TIMESTAMP NOT NULL, status TEXT NOT NULL)")
            await db.execute("CREATE TABLE IF NOT EXISTS blocks (blocker_id BIGINT NOT NULL, blocked_id BIGINT NOT NULL, PRIMARY KEY (blocker_id, blocked_id))")
            await db.commit()
        logger.info(f"Database '{config.CONFESSIONS_DB_FILE}' is ready.")

    # --- REWRITTEN User Lookup Helper ---
    async def _find_target_user(self, interaction: discord.Interaction, user_input: str) -> Optional[discord.User]:
        """Finds a user from a string input (ID, mention, modern username, or Name#Tag)."""
        # 1. By ID or Mention (most reliable)
        user_id_match = re.match(r'<?@?!?(\d+)>?$', user_input)
        if user_id_match:
            try:
                user = await self.bot.fetch_user(int(user_id_match.group(1)))
                logger.debug(f"Found user by ID: {user}")
                return user
            except discord.NotFound:
                logger.debug(f"User ID {user_id_match.group(1)} not found.")
                return None

        # 2. By Modern Username (Global Search) or Old Name#Tag
        # discord.utils.get is case-sensitive for Name#Tag but case-insensitive for modern usernames.
        # This is a good general-purpose search.
        clean_input = user_input.strip()
        found_user = discord.utils.find(lambda u: str(u).lower() == clean_input.lower(), self.bot.users)
        if found_user:
            logger.debug(f"Found user by global name match: {found_user}")
            return found_user
        
        # 3. Fallback: Search by name/nickname in mutual guilds (less reliable but good backup)
        logger.debug(f"Global name search failed. Falling back to mutual guild search for '{clean_input}'.")
        for guild in self.bot.guilds:
            if guild.get_member(interaction.user.id): # Check if the command author is in this guild
                # get_member_named is case-sensitive for name but insensitive for nickname
                member = guild.get_member_named(clean_input)
                if member:
                    logger.debug(f"Found user by server name/nickname: {member}")
                    return member
        
        logger.warning(f"Could not find user '{clean_input}' through any method.")
        return None

    @app_commands.command(name="confess", description="Send an anonymous confession to a user.")
    @app_commands.describe(target_user="User's ID, @Mention, or unique username.", message="Your confession message.")
    async def confess(self, interaction: discord.Interaction, target_user: str, message: str):
        # (This command logic is now correct thanks to the updated _find_target_user)
        await interaction.response.defer(ephemeral=True)
        target = await self._find_target_user(interaction, target_user)
        if not target: await interaction.followup.send("❌ Could not find that user. Please use their full unique username, @mention, or User ID.", ephemeral=True); return
        if target.bot: await interaction.followup.send("You cannot confess to a bot.", ephemeral=True); return
        if target.id == interaction.user.id: await interaction.followup.send("You cannot confess to yourself.", ephemeral=True); return
        if not any(g.get_member(target.id) for g in self.bot.guilds if g.get_member(interaction.user.id)):
            await interaction.followup.send("You and the target user must share at least one server with me.", ephemeral=True); return
        
        async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?", (target.id, interaction.user.id)) as cursor:
                if await cursor.fetchone(): await interaction.followup.send("This user is not accepting confessions at this time.", ephemeral=True); return
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (target.id,)) as cursor:
                target_settings = await cursor.fetchone()
            timestamp = datetime.datetime.now(datetime.timezone.utc)
            cursor = await db.execute("INSERT INTO confessions (confessor_id, target_id, confessor_name, target_name, message_content, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?)", (interaction.user.id, target.id, str(interaction.user), str(target), message, timestamp, 'pending_approval'))
            confession_id = cursor.lastrowid
            
            # Logic Flow
            if target_settings and not target_settings['can_receive']:
                await db.execute("UPDATE confessions SET status = ? WHERE id = ?", ('denied', confession_id))
                await interaction.followup.send("This user is not accepting confessions.", ephemeral=True)
            elif target_settings and target_settings['can_receive']:
                try:
                    embed = discord.Embed(title="💌 Anonymous Confession", description=f">>> {message}", color=discord.Color.pink(), timestamp=timestamp)
                    await target.send(embed=embed, view=ConfessionView(self.bot, confession_id))
                    await db.execute("UPDATE confessions SET status = ? WHERE id = ?", ('sent', confession_id))
                    await interaction.followup.send("✅ Your confession has been sent!", ephemeral=True)
                except discord.Forbidden: await interaction.followup.send("This user isn't accepting confessions (DMs may be closed).", ephemeral=True)
            else: # First Time
                try:
                    embed = discord.Embed(title="💌 New Confession Request", description="Someone wants to send you an anonymous confession!\n\n**Do you want to receive it?**\nAccepting will show you this one message and allow future confessions.", color=discord.Color.blurple())
                    embed.add_field(name="How does this work?", value="- **Anonymous:** You won't know who sent it.\n- **Reply:** You can reply to confessions.\n- **Block:** You can block any sender.\n- **Control:** Use `/confessions deactivate`.", inline=False)
                    view = ApprovalView(self.bot); message = await target.send(embed=embed, view=view); view.message = message
                    await interaction.followup.send("The user must approve confessions first. A request has been sent.", ephemeral=True)
                except discord.Forbidden:
                    await db.execute("UPDATE confessions SET status = ? WHERE id = ?", ('denied', confession_id))
                    await interaction.followup.send("This user isn't accepting confessions (DMs may be closed).", ephemeral=True)
            await db.commit()
    
    # (Management commands unchanged)
    confessions_group = app_commands.Group(name="confessions", description="Manage your confession settings.")
    @confessions_group.command(name="activate", description="Allow users to send you anonymous confessions.")
    async def activate(self, interaction: discord.Interaction):
        async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db: await db.execute("INSERT OR REPLACE INTO users (user_id, can_receive, has_been_prompted) VALUES (?, ?, ?)", (interaction.user.id, True, True)); await db.commit()
        await interaction.response.send_message("✅ You will now receive anonymous confessions.", ephemeral=True)
    @confessions_group.command(name="deactivate", description="Stop receiving all anonymous confessions.")
    async def deactivate(self, interaction: discord.Interaction):
        async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db: await db.execute("INSERT OR REPLACE INTO users (user_id, can_receive, has_been_prompted) VALUES (?, ?, ?)", (interaction.user.id, False, True)); await db.commit()
        await interaction.response.send_message("❌ You will no longer receive new confessions.", ephemeral=True)
    @confessions_group.command(name="unblock_all", description="Removes all blocks you have placed on anonymous confessors.")
    async def unblock_all(self, interaction: discord.Interaction):
        view = UnblockAllView()
        await interaction.response.send_message("Are you sure you want to unblock **all** confessors?", view=view, ephemeral=True)
        await view.wait()
        if view.confirmed:
            async with aiosqlite.connect(config.CONFESSIONS_DB_FILE) as db:
                cursor = await db.execute("DELETE FROM blocks WHERE blocker_id = ?", (interaction.user.id,)); await db.commit()
                deleted_count = cursor.rowcount
            await interaction.edit_original_response(content=f"✅ Successfully unblocked {deleted_count} user(s).", view=None)
        else: await interaction.edit_original_response(content="Action cancelled.", view=None)

async def setup(bot: RadioBot):
    await bot.add_cog(Confessions(bot))