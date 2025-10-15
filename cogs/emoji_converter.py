# ./discord-radio-bot-modular/cogs/emoji_converter.py

import discord
from discord.ext import commands
import logging
import aiohttp
import io # For handling bytes in memory
from PIL import Image, ImageSequence, UnidentifiedImageError
import re
from typing import Optional

import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.emoji_converter')

MAX_EMOJI_SIZE_KB = 256.0
TARGET_RESIZE_DIM = 128

class EmojiConverter(commands.Cog):
    """Cog for converting GIFs to server emojis using a quality-first cascade."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("EmojiConverter Cog initialized.")

    # --- UPDATED Helper for processing with Transparency Fix ---
    def _process_gif(self, image_bytes: bytes, optimize: bool) -> tuple[Optional[bytes], Optional[str]]:
        """
        Processes GIF bytes: Resizes and optionally optimizes, preserving transparency.
        Returns (processed_bytes, error_message)
        """
        process_type = "size" if optimize else "quality"
        logger.debug(f"Processing GIF for {process_type}...")
        try:
            with Image.open(io.BytesIO(image_bytes)) as im:
                frames = []
                durations = []
                loop_count = im.info.get('loop', 0)
                
                for frame in ImageSequence.Iterator(im):
                    # Create a new RGBA canvas for each frame to handle disposal correctly
                    new_frame = Image.new('RGBA', im.size)
                    # Paste the original frame onto the new transparent canvas
                    # This correctly handles GIFs with palettes and transparency
                    new_frame.paste(frame, (0, 0), frame.convert('RGBA'))
                    
                    # Resize with high-quality downsampling
                    new_frame.thumbnail((TARGET_RESIZE_DIM, TARGET_RESIZE_DIM), Image.Resampling.LANCZOS)
                    
                    frames.append(new_frame)
                    durations.append(frame.info.get('duration', 100))

                output_bytes_io = io.BytesIO()
                if frames:
                    # When saving, Pillow's default GIF saver handles transparency well if the frames are RGBA.
                    save_kwargs = {
                        "format": 'GIF',
                        "save_all": True,
                        "optimize": optimize,
                        "duration": durations,
                        "loop": loop_count,
                        # --- FIX: Tell Pillow to include the transparency color index ---
                        "transparency": 0,
                        "disposal": 2 # Dispose to background color (which will be transparent)
                    }
                    if len(frames) > 1:
                        save_kwargs["append_images"] = frames[1:]
                    
                    frames[0].save(output_bytes_io, **save_kwargs)
                    output_bytes = output_bytes_io.getvalue()
                    final_size_kb = len(output_bytes) / 1024

                    logger.info(f"Processed GIF (optimize={optimize}): {final_size_kb:.2f} KB")
                    if final_size_kb > MAX_EMOJI_SIZE_KB:
                        return None, f"Processed size ({final_size_kb:.1f} KB) is still over the {MAX_EMOJI_SIZE_KB} KB limit."
                    return output_bytes, None
                else:
                    return None, "Error: No frames were processed."

        except UnidentifiedImageError:
             return None, "Error: Could not identify the image file. Is it a valid image/GIF URL?"
        except Exception as e:
            logger.exception(f"Error during GIF processing (optimize={optimize}): {e}")
            return None, f"An unexpected error occurred while processing the GIF: {e}"

    # --- REWRITTEN Command with New Cascade Logic ---
    @commands.hybrid_command(name="convert", description="Converts a GIF URL to a high-quality server emoji.")
    @discord.app_commands.describe(link="The direct URL to the GIF.", name="The emoji name (letters, numbers, underscore).")
    @commands.guild_only()
    @commands.has_permissions(manage_emojis_and_stickers=True)
    @commands.bot_has_permissions(manage_emojis_and_stickers=True)
    async def convert(self, ctx: commands.Context, link: str, name: str):
        if not ctx.guild or not self.bot.http_session: return
        if ctx.interaction: await ctx.defer(ephemeral=False)
        if not re.match(r"^[a-zA-Z0-9_]{2,32}$", name):
            await ctx.send("Error: Invalid emoji name. Must be 2-32 letters, numbers, or underscores.", ephemeral=True); return

        # --- Download GIF ---
        try:
            logger.info(f"[{ctx.guild.id}] Downloading GIF: {link}")
            async with self.bot.http_session.get(link) as response:
                if response.status != 200: await ctx.send(f"Error: Download failed (Status: {response.status}).", ephemeral=True); return
                original_bytes = await response.read()
        except Exception as e:
            await ctx.send(f"Error: Failed to download from URL: {e}", ephemeral=True); return
        logger.info(f"[{ctx.guild.id}] Downloaded {len(original_bytes) / 1024:.2f} KB.")

        # --- Cascade Step 1: Attempt Direct Upload (Trust Discord) ---
        logger.info(f"[{ctx.guild.id}] Step 1: Attempting direct upload of original file...")
        try:
            emoji = await ctx.guild.create_custom_emoji(name=name, image=original_bytes, reason=f"Emoji created by {ctx.author}")
            logger.info(f"[{ctx.guild.id}] Success! Direct upload worked.")
            await ctx.send(f"Successfully created emoji (Discord handled processing): {emoji}")
            return # SUCCESS!
        except discord.HTTPException as e:
            # Check for the specific "too large" error.
            if "File cannot be larger than" in e.text or "request entity too large" in e.text:
                logger.info(f"[{ctx.guild.id}] Step 1 failed: File is too large for direct upload. Proceeding to Step 2.")
                # Continue to the next step in the cascade
                pass
            else: # It failed for a different reason (e.g., emoji limit, invalid name)
                logger.error(f"[{ctx.guild.id}] Step 1 failed for a non-size reason: {e.status} - {e.text}")
                if "Maximum number of emojis reached" in e.text: await ctx.send("Error: This server has reached its emoji limit.", ephemeral=True)
                elif "name" in e.text: await ctx.send(f"Error: Invalid emoji name '{name}' according to Discord.", ephemeral=True)
                else: await ctx.send(f"Error: Discord rejected the emoji (Code: {e.status}).", ephemeral=True)
                return # Stop the process
        except Exception as e:
            logger.exception(f"[{ctx.guild.id}] Step 1 failed with an unexpected error.")
            await ctx.send(f"An unexpected error occurred during direct upload: {e}", ephemeral=True)
            return

        # --- Cascade Step 2: Resize for Quality (optimize=False) ---
        await ctx.send("Original file is too large for direct upload. Resizing for max quality, please wait...")
        quality_bytes, error_msg = self._process_gif(original_bytes, optimize=False)
        
        if quality_bytes:
            logger.info(f"[{ctx.guild.id}] Step 2: Quality resize successful. Attempting upload...")
            try:
                emoji = await ctx.guild.create_custom_emoji(name=name, image=quality_bytes, reason=f"Emoji created by {ctx.author} (quality resize)")
                logger.info(f"[{ctx.guild.id}] Success! Quality resize upload worked.")
                await ctx.send(f"Successfully created emoji (resized for quality): {emoji}")
                return # SUCCESS!
            except discord.HTTPException as e:
                if "File cannot be larger than" in e.text or "request entity too large" in e.text:
                    logger.warning(f"[{ctx.guild.id}] Step 2 failed: Quality resize is still too large. Proceeding to Step 3.")
                    pass # Continue to the next step
                else: # Other error
                    logger.error(f"[{ctx.guild.id}] Step 2 failed for a non-size reason: {e.status} - {e.text}")
                    await ctx.send(f"Error after resizing: Discord rejected the emoji (Code: {e.status}).", ephemeral=True)
                    return
        else:
            logger.error(f"[{ctx.guild.id}] Step 2 failed: Processing error. {error_msg}")
            # Proceed to Step 3 anyway, optimization might fix some processing errors
        
        # --- Cascade Step 3: Resize for Size (optimize=True) ---
        await ctx.send("Quality resize was still too large. Last attempt: optimizing for smaller file size...")
        optimized_bytes, error_msg = self._process_gif(original_bytes, optimize=True)

        if optimized_bytes:
            logger.info(f"[{ctx.guild.id}] Step 3: Optimized resize successful. Attempting upload...")
            try:
                emoji = await ctx.guild.create_custom_emoji(name=name, image=optimized_bytes, reason=f"Emoji created by {ctx.author} (optimized resize)")
                logger.info(f"[{ctx.guild.id}] Success! Optimized resize upload worked.")
                await ctx.send(f"Successfully created emoji (resized for size): {emoji}")
                return # SUCCESS!
            except discord.HTTPException as e:
                 logger.error(f"[{ctx.guild.id}] Step 3 failed. All attempts exhausted. Final error: {e.status} - {e.text}")
                 await ctx.send(f"❌ Failed to create emoji after optimization. The GIF is too complex to fit within Discord's {MAX_EMOJI_SIZE_KB} KB limit.", ephemeral=True)
                 return
        
        # --- Cascade Step 4: Fail ---
        logger.error(f"[{ctx.guild.id}] All processing steps failed for emoji '{name}'. Final error from processing: {error_msg}")
        await ctx.send(f"❌ Failed to create emoji. The GIF could not be processed or is too complex for Discord's limits.")

async def setup(bot: RadioBot):
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        logger.critical("Pillow library not found. EmojiConverter Cog requires 'Pillow'.")
        return
    await bot.add_cog(EmojiConverter(bot))