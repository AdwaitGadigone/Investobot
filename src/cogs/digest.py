import time

import discord
from discord import app_commands
from discord.ext import commands

from config import HEAVY_COOLDOWN_SECONDS, LIGHT_COOLDOWN_SECONDS
from cogs.scheduler import DigestView, _build_digest_embed
from services import db

# Discord slash commands don't work in DMs unless the bot's installed as a user app, this is the plain-text
# workaround: DM the bot one of these and get the same thing /digest_now sends.
_DM_TRIGGERS = {"digest", "!digest"}


async def _send_one_digest(user: discord.User, guild_id: int, content: str) -> bool:
    # Shared by /digest_now and the DM trigger below, so both stay identical instead of drifting apart.
    embed = await _build_digest_embed(guild_id, user.id, content)
    try:
        await user.send(embed=embed, view=DigestView(guild_id, user.id, content))
        return True
    except discord.Forbidden:
        return False


class Digest(commands.Cog):
    # Just the opt-in toggle, the actual daily DM is sent on a schedule in cogs/scheduler.py.

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-user, in-memory, resets on restart, same tradeoff as every other cache in this codebase,
        # a raw message listener has no built-in cooldown like app_commands.checks.cooldown does.
        self._dm_cooldowns: dict[int, float] = {}

    @app_commands.command(name="digest", description="Toggle your daily DM, or change what it includes")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(content="What the daily DM should include, leave blank to just toggle on/off")
    @app_commands.choices(
        content=[
            app_commands.Choice(name="Watchlist", value="watchlist"),
            app_commands.Choice(name="Portfolio", value="portfolio"),
            app_commands.Choice(name="Both", value="both"),
        ]
    )
    async def digest(self, interaction: discord.Interaction, content: app_commands.Choice[str] = None):
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        was_enabled = await db.is_digest_enabled(guild_id, user_id)

        # No content given and already on: bare /digest is the plain off-switch, same as before this option existed.
        if content is None and was_enabled:
            await db.disable_digest(guild_id, user_id)
            await interaction.response.send_message("Turned off your daily digest DM.")
            return

        if not was_enabled:
            # Sends a test DM before saving anything, so this can't claim success for someone whose DMs are closed.
            try:
                await interaction.user.send(
                    "You're signed up for Investo's daily digest. I'll DM you a summary "
                    "here each morning. Turn it off anytime with `/digest`."
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I couldn't DM you, check that your privacy settings allow DMs from server members."
                )
                return
            await db.enable_digest(guild_id, user_id, content.value if content else "watchlist")
            await interaction.response.send_message(
                f"Daily digest turned on ({content.name if content else 'Watchlist'}), check your DMs!"
            )
            return

        # Already on, content given: update the preference in place instead of toggling off.
        await db.set_digest_content(guild_id, user_id, content.value)
        await interaction.response.send_message(f"Daily digest now includes: **{content.name}**.")

    @app_commands.command(
        name="digest_now",
        description="Send yourself the daily digest right now, for testing or if the morning one didn't show up",
    )
    @app_commands.checks.cooldown(1, HEAVY_COOLDOWN_SECONDS)
    async def digest_now(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        # Works whether or not the daily DM is turned on, "both" matches DigestContentSelect's own default.
        content = await db.get_digest_content(guild_id, user_id) or "both"

        await interaction.response.defer(ephemeral=True)
        sent = await _send_one_digest(interaction.user, guild_id, content)
        if not sent:
            await interaction.followup.send(
                "Couldn't DM you, check that your privacy settings allow DMs from server members."
            )
            return
        await interaction.followup.send("Sent, check your DMs.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None:
            return
        if message.content.strip().lower() not in _DM_TRIGGERS:
            return

        now = time.monotonic()
        last = self._dm_cooldowns.get(message.author.id, 0.0)
        if now - last < HEAVY_COOLDOWN_SECONDS:
            await message.channel.send(f"Slow down, you can use this again in {HEAVY_COOLDOWN_SECONDS - (now - last):.0f}s.")
            return
        self._dm_cooldowns[message.author.id] = now

        # A DM has no server context to read guild_id from, unlike the slash command, this looks up
        # every server this person has the digest turned on in instead.
        optins = await db.get_digest_optins_for_user(message.author.id)
        if not optins:
            await message.channel.send(
                "You're not signed up for the daily digest anywhere yet, run `/digest` in a server first."
            )
            return

        for guild_id, content in optins:
            sent = await _send_one_digest(message.author, guild_id, content)
            if not sent:
                continue
            if len(optins) > 1:
                # We're already mid-DM with them, so this can't be the DM-permissions failure /digest_now
                # guards against, the only ambiguity left worth calling out is which server this one was for.
                guild = self.bot.get_guild(guild_id)
                await message.channel.send(f"(that one was for **{guild.name if guild else guild_id}**)")


async def setup(bot: commands.Bot):
    await bot.add_cog(Digest(bot))
