# ./discord-radio-bot-modular/cogs/dev_tools.py

import discord
from discord.ext import commands
from discord import app_commands
import logging
import google.generativeai as genai
from typing import Optional

import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.dev_tools')

class DevTools(commands.Cog):
    """Developer-only commands for diagnostics and bot management."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        if config.GEMINI_API_KEY:
            try:
                genai.configure(api_key=config.GEMINI_API_KEY)
                logger.info("DevTools Cog: Gemini configured.")
            except Exception as e:
                logger.error(f"DevTools Cog: Failed to configure Gemini: {e}")
        else:
            logger.warning("DevTools Cog: No GEMINI_API_KEY found.")

    async def cog_check(self, ctx: commands.Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    @commands.hybrid_command(name="listmodels", description="[Owner Only] Lists available Gemini models.")
    async def listmodels(self, ctx: commands.Context):
        """Lists all generative models available to the API key, paginating if necessary."""
        if not config.GEMINI_API_KEY:
            await ctx.send("❌ Gemini API key is not configured.", ephemeral=True)
            return

        # Defer ephemerally as this is an owner-only command
        await ctx.defer(ephemeral=True)

        try:
            embed = discord.Embed(
                title="🤖 Available Gemini Models",
                description="List of models your API key can use with the `generateContent` method.",
                color=discord.Color.blue()
            )
            
            supported_models = []
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    supported_models.append(m.name)
            
            if not supported_models:
                embed.add_field(name="No Supported Models Found", value="Could not find any models supporting 'generateContent' for your API key.", inline=False)
            else:
                # --- FIX: Split the long list into multiple fields to avoid exceeding the character limit ---
                fields = []
                current_field_value = ""
                for model_name in supported_models:
                    line_to_add = f"**`{model_name}`**\n"
                    # Check if adding the next line would exceed the 1024 character limit
                    if len(current_field_value) + len(line_to_add) > 1024:
                        # If so, finalize the current field and start a new one
                        fields.append(current_field_value)
                        current_field_value = ""
                    current_field_value += line_to_add
                
                # Add the last (or only) field to the list
                if current_field_value:
                    fields.append(current_field_value)

                # Add the generated fields to the embed
                for i, field_content in enumerate(fields):
                    field_name = f"Found {len(supported_models)} Supported Models (Part {i+1})" if len(fields) > 1 else f"Found {len(supported_models)} Supported Models"
                    embed.add_field(name=field_name, value=field_content, inline=False)

            embed.set_footer(text="Copy the full name (e.g., 'models/gemini-1.0-pro') into ai_assistant.py.")
            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.exception("Failed to list Gemini models.")
            # Send the error message in the interaction followup
            await ctx.send(f"An error occurred while fetching models: ```{e}```", ephemeral=True)


async def setup(bot: RadioBot):
    await bot.add_cog(DevTools(bot))