# ./discord-radio-bot-modular/cogs/emoji_converter.py

import discord
from discord.ext import commands
import logging
import aiohttp
import io # For handling bytes in memory
from PIL import Image, ImageSequence, UnidentifiedImageError # Explicitly import error too
import re # For basic name validation
from typing import Optional # <--- ADD THIS IMPORT

# Import configuration and the main bot class type hint
import config
from core.bot import RadioBot # For type hinting

logger = logging.getLogger('discord_bot.cogs.emoji_converter')

MAX_EMOJI_SIZE_KB = 256
TARGET_RESIZE_DIM = 128 # Target dimension for resizing (e.g., 128x128)

class EmojiConverter(commands.Cog):
    """Cog for converting GIFs to server emojis."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        logger.info("EmojiConverter Cog initialized.")

    # --- Helper for processing ---
    # Type hint now valid because Optional is imported
    def _process_gif(self, image_bytes: bytes) -> tuple[Optional[bytes], Optional[str]]:
        """
        Processes GIF bytes: Resizes, optimizes, checks size.
        Returns (processed_bytes, error_message)
        """
        try:
            with Image.open(io.BytesIO(image_bytes)) as im:
                logger.debug(f"Opened image. Format: {im.format}, Animated: {getattr(im, 'is_animated', False)}")

                frames = []
                durations = [] # Store durations for each frame

                # Preserve transparency and background disposal if possible
                transparency = im.info.get('transparency', None)
                disposal = im.info.get('disposal', 2) # Default dispose to background
                loop_count = im.info.get('loop', 0) # 0 means loop indefinitely

                if getattr(im, 'is_animated', False):
                    logger.debug("Processing animated GIF...")
                    total_duration = 0
                    for frame in ImageSequence.Iterator(im):
                        # Ensure frame is RGBA for consistency, handling palette modes
                        frame = frame.convert('RGBA')

                        # Calculate aspect ratio preserving resize
                        frame.thumbnail((TARGET_RESIZE_DIM, TARGET_RESIZE_DIM), Image.Resampling.LANCZOS)
                        frames.append(frame)

                        # Get duration, default if missing
                        duration = frame.info.get('duration', 100) # Default to 100ms (10fps)
                        durations.append(duration)
                        total_duration += duration
                    logger.debug(f"Processed {len(frames)} frames. Total duration: {total_duration}ms")

                else:
                    # Handle static image (convert to GIF format)
                    logger.debug("Processing static image...")
                    im = im.convert('RGBA')
                    im.thumbnail((TARGET_RESIZE_DIM, TARGET_RESIZE_DIM), Image.Resampling.LANCZOS)
                    frames.append(im)
                    durations.append(100) # Static image, arbitrary duration

                # Save processed frames back to bytes
                output_bytes_io = io.BytesIO()
                if frames:
                    save_kwargs = {
                        "format": 'GIF',
                        "save_all": True,
                        "optimize": True, # Enable optimization
                        "duration": durations, # List of durations per frame
                        "loop": loop_count, # Loop count
                        # "transparency": transparency, # Pillow might handle this automatically with RGBA
                        "disposal": disposal # How to handle frame disposal
                    }
                    # append_images only needed if more than one frame
                    if len(frames) > 1:
                        save_kwargs["append_images"] = frames[1:]

                    frames[0].save(output_bytes_io, **save_kwargs)

                    output_bytes = output_bytes_io.getvalue()
                    final_size_kb = len(output_bytes) / 1024
                    logger.info(f"Processed GIF size: {final_size_kb:.2f} KB")

                    if final_size_kb > MAX_EMOJI_SIZE_KB:
                        return None, f"Error: Processed GIF is too large ({final_size_kb:.1f} KB). Max size is {MAX_EMOJI_SIZE_KB} KB."
                    return output_bytes, None
                else:
                    return None, "Error: No frames processed."

        except UnidentifiedImageError:
             return None, "Error: Could not identify the image file. Is the URL correct and pointing to an image/GIF?"
        except Exception as e:
            logger.exception(f"Error during GIF processing: {e}")
            return None, f"Error processing GIF: {e}"

    # --- Command ---
    @commands.hybrid_command(name="convert", description="Converts a GIF URL to a server emoji.")
    @discord.app_commands.describe(
        link="The direct URL to the GIF.",
        name="The desired name for the emoji (letters, numbers, underscore only)."
    )
    @commands.guild_only() # Emojis are server-specific
    @commands.has_permissions(manage_emojis_and_stickers=True) # Check user permissions
    @commands.bot_has_permissions(manage_emojis_and_stickers=True) # Check bot permissions
    async def convert(self, ctx: commands.Context, link: str, name: str):
        """Converts a GIF from a URL into a server emoji."""
        # Ensure context has guild and necessary components
        if not ctx.guild: return # Should be caught by guild_only, but good practice
        if not self.bot.http_session: await ctx.send("Error: Bot's HTTP session not ready.", ephemeral=True); return

        if ctx.interaction: await ctx.defer(ephemeral=False) # Defer publicly

        # --- Validate Emoji Name ---
        # Basic validation: letters, numbers, underscore, 2-32 chars
        if not re.match(r"^[a-zA-Z0-9_]{2,32}$", name):
            await ctx.send(f"Error: Invalid emoji name '{name}'. Must be 2-32 characters using only letters, numbers, and underscores (_).", ephemeral=True)
            return

        # --- Download GIF ---
        logger.info(f"[{ctx.guild.id}] Attempting GIF download: {link}")
        try:
            async with self.bot.http_session.get(link) as response:
                if response.status != 200:
                    await ctx.send(f"Error: Could not download GIF. Status code: {response.status}", ephemeral=True)
                    return
                # Check content type if possible (optional but good)
                # content_type = response.headers.get('Content-Type', '').lower()
                # if 'gif' not in content_type and 'image' not in content_type:
                #     await ctx.send(f"Error: URL does not appear to be an image or GIF (Content-Type: {content_type}).", ephemeral=True)
                #     return

                image_bytes = await response.read()
                logger.debug(f"[{ctx.guild.id}] Downloaded {len(image_bytes)} bytes.")

        except aiohttp.ClientError as e:
            logger.warning(f"[{ctx.guild.id}] Network error downloading GIF {link}: {e}")
            await ctx.send(f"Error: Network error downloading GIF: {e}", ephemeral=True)
            return
        except Exception as e:
            logger.exception(f"[{ctx.guild.id}] Unexpected error downloading GIF {link}: {e}")
            await ctx.send(f"Error: Unexpected error during download: {e}", ephemeral=True)
            return

        # --- Process GIF ---
        logger.info(f"[{ctx.guild.id}] Processing GIF for emoji '{name}'...")
        processed_bytes, error_msg = self._process_gif(image_bytes)

        if error_msg:
            await ctx.send(error_msg, ephemeral=True) # Send processing error to user
            return

        if not processed_bytes:
             await ctx.send("Error: GIF processing failed for an unknown reason.", ephemeral=True)
             return

        # --- Upload Emoji ---
        logger.info(f"[{ctx.guild.id}] Attempting to upload emoji '{name}' ({len(processed_bytes) / 1024:.1f} KB)")
        try:
            emoji = await ctx.guild.create_custom_emoji(
                name=name,
                image=processed_bytes,
                reason=f"Emoji created by {ctx.author} using /convert command"
            )
            logger.info(f"[{ctx.guild.id}] Successfully created emoji: {emoji.name} ({emoji.id})")
            await ctx.send(f"Successfully created emoji: {emoji}") # Show the emoji!

        except discord.HTTPException as e:
            logger.error(f"[{ctx.guild.id}] Failed to upload emoji '{name}': {e.status} - {e.text}", exc_info=False if e.status == 400 else True) # Log full trace unless it's a standard bad request
            # Provide more specific feedback based on error code if possible
            if e.status == 400: # Bad Request - often name/size issues or slots full
                 if "Maximum number of emojis reached" in e.text:
                     await ctx.send("Error: Could not create emoji. The server has reached its maximum emoji limit.", ephemeral=True)
                 elif "Invalid Form Body" in e.text and "name" in e.text: # Check if error text mentions name
                     await ctx.send(f"Error: Invalid emoji name '{name}' according to Discord.", ephemeral=True)
                 elif "Invalid Form Body" in e.text and ("File cannot be larger than" in e.text or "width" in e.text or "height" in e.text): # Check if error text mentions size
                      await ctx.send(f"Error: Processed GIF size/dimensions rejected by Discord (is it definitely under 256KB?).", ephemeral=True)
                 else: # Generic Bad Request
                     await ctx.send(f"Error: Discord rejected the emoji (Code: {e.status}). Is the name valid and unique? Is the file size okay?", ephemeral=True)
            elif e.status == 403: # Forbidden
                 await ctx.send("Error: I don't have the required 'Manage Expressions' permission to create emojis.", ephemeral=True)
            else: # Other HTTP errors
                await ctx.send(f"Error: Failed to upload emoji (HTTP {e.status}). Please try again later.", ephemeral=True)

        except Exception as e:
            logger.exception(f"[{ctx.guild.id}] Unexpected error uploading emoji '{name}': {e}")
            await ctx.send(f"An unexpected error occurred during emoji upload: {e}", ephemeral=True)


# Setup function for discord.py to load the cog
async def setup(bot: RadioBot):
    # Pillow import check - optional but can give earlier warning
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        logger.critical("Pillow library not found. EmojiConverter Cog requires 'Pillow'. Install with 'pip install Pillow'")
        # You might choose to raise an error here to prevent loading
        # raise commands.ExtensionFailed("EmojiConverter", original=ImportError("Pillow not found"))
        return # Or just log and don't load the cog

    await bot.add_cog(EmojiConverter(bot))