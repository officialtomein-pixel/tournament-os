"""
Feature Flags cog — manage per-tournament feature toggles.

Commands:
  /flags list      — show current feature flags for a tournament
  /flags set       — enable or disable a feature flag
  /flags webhooks_add    — register an outbound webhook URL
  /flags webhooks_remove — remove a webhook URL
  /flags webhooks_list   — list registered webhooks
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging

from app.database.models.staff import StaffRole

logger = logging.getLogger(__name__)

flags_group = app_commands.Group(
    name="flags",
    description="Manage tournament feature flags and webhooks (Tournament Admin only)",
)

# Well-known flags with descriptions shown in /flags list
_KNOWN_FLAGS = {
    "score_auto_approval": "Auto-approve match scores when both teams agree (default: on)",
    "checkin_required": "Require team check-in before going LIVE (default: on)",
    "allow_score_edit": "Allow captains to edit submitted scores before opponent confirms",
    "ai_moderation": "Use AI to assist with dispute moderation",
    "solo_auto_team": "Auto-create a solo team when a player without a team registers",
    "snapshot_on_round": "Take a snapshot after every round completes",
}


class FlagsCog(commands.Cog, name="flags"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.tree.add_command(flags_group)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(flags_group.name)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _require_admin_and_tournament(session, interaction: discord.Interaction, tournament_id: str):
    from app.database.models.guild import Guild
    from app.database.repositories.tournament import TournamentRepository
    from app.bot.helpers.permissions import has_permission
    from sqlalchemy import select

    if not await has_permission(session, interaction.user, str(interaction.guild_id), StaffRole.TOURNAMENT_ADMIN):
        await interaction.followup.send(
            embed=discord.Embed(title="❌ Permission Denied", description="Tournament Admin role required.", color=discord.Color.red()),
            ephemeral=True,
        )
        return None, None

    guild_q = select(Guild).where(Guild.discord_guild_id == str(interaction.guild_id), Guild.deleted_at.is_(None))
    guild = (await session.execute(guild_q)).scalar_one_or_none()
    if not guild:
        await interaction.followup.send("Server not registered.", ephemeral=True)
        return None, None

    t_repo = TournamentRepository(session)
    tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
    if not tournament:
        await interaction.followup.send("Tournament not found.", ephemeral=True)
        return None, None

    return guild, tournament


# ── /flags list ───────────────────────────────────────────────────────────────

@flags_group.command(name="list", description="List all feature flags for a tournament")
@app_commands.describe(tournament_id="Tournament ID")
async def flags_list(interaction: discord.Interaction, tournament_id: str) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        _, tournament = await _require_admin_and_tournament(session, interaction, tournament_id)
        if not tournament:
            return
        flags: dict = tournament.feature_flags or {}

    embed = discord.Embed(
        title=f"🚩 Feature Flags — {tournament.name}",
        color=discord.Color.blurple(),
    )
    if not flags and not _KNOWN_FLAGS:
        embed.description = "No feature flags configured."
    else:
        # Show all known flags with their current value
        for key, desc in _KNOWN_FLAGS.items():
            val = flags.get(key)
            if val is None:
                icon = "⬜"
                display = "default"
            elif val:
                icon = "✅"
                display = "enabled"
            else:
                icon = "❌"
                display = "disabled"
            embed.add_field(name=f"{icon} `{key}`", value=f"**{display}**\n{desc}", inline=False)
        # Custom flags not in known list
        for key, val in flags.items():
            if key not in _KNOWN_FLAGS:
                embed.add_field(name=f"⚙️ `{key}`", value=str(val), inline=True)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /flags set ─────────────────────────────────────────────────────────────────

@flags_group.command(name="set", description="Enable or disable a feature flag")
@app_commands.describe(
    tournament_id="Tournament ID",
    flag="Feature flag name (e.g. score_auto_approval)",
    enabled="True to enable, false to disable",
)
async def flags_set(
    interaction: discord.Interaction,
    tournament_id: str,
    flag: str,
    enabled: bool,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.bot.helpers.formatters import success_embed

    async with AsyncSessionLocal() as session:
        async with session.begin():
            _, tournament = await _require_admin_and_tournament(session, interaction, tournament_id)
            if not tournament:
                return

            flags = dict(tournament.feature_flags or {})
            flags[flag] = enabled
            tournament.feature_flags = flags

    icon = "✅" if enabled else "❌"
    await interaction.followup.send(
        embed=success_embed(
            f"Flag `{flag}` set to **{icon} {'enabled' if enabled else 'disabled'}** for **{tournament.name}**.",
            title="Feature Flag Updated",
        ),
        ephemeral=True,
    )
    logger.info("flags.set: tournament=%s flag=%s value=%s by=%s", tournament_id[:8], flag, enabled, interaction.user.id)


# ── /flags webhooks_add ───────────────────────────────────────────────────────

@flags_group.command(name="webhooks_add", description="Register an outbound webhook URL for tournament events")
@app_commands.describe(
    tournament_id="Tournament ID",
    url="Webhook URL (HTTPS recommended)",
    events="Comma-separated event names, or * for all (e.g. TournamentStatusChanged,MatchCompleted)",
    secret="Optional HMAC signing secret",
)
async def webhooks_add(
    interaction: discord.Interaction,
    tournament_id: str,
    url: str,
    events: str = "*",
    secret: str = "",
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.bot.helpers.formatters import success_embed, error_embed

    if not url.startswith("http"):
        await interaction.followup.send(embed=error_embed("URL must start with http:// or https://"), ephemeral=True)
        return

    event_list = [e.strip() for e in events.split(",") if e.strip()] or ["*"]

    async with AsyncSessionLocal() as session:
        async with session.begin():
            _, tournament = await _require_admin_and_tournament(session, interaction, tournament_id)
            if not tournament:
                return

            config = dict(tournament.channel_config or {})
            existing: list[dict] = list(config.get("webhooks", []))

            if any(wh.get("url") == url for wh in existing):
                await interaction.followup.send(embed=error_embed("This URL is already registered."), ephemeral=True)
                return

            new_wh: dict = {"url": url, "events": event_list}
            if secret:
                new_wh["secret"] = secret

            existing.append(new_wh)
            config["webhooks"] = existing
            tournament.channel_config = config

    await interaction.followup.send(
        embed=success_embed(
            f"Webhook registered for **{tournament.name}**.\n"
            f"URL: `{url[:60]}`\n"
            f"Events: `{', '.join(event_list)}`",
            title="✅ Webhook Added",
        ),
        ephemeral=True,
    )
    logger.info("webhooks.add: tournament=%s url=%s events=%s", tournament_id[:8], url[:60], event_list)


# ── /flags webhooks_remove ────────────────────────────────────────────────────

@flags_group.command(name="webhooks_remove", description="Remove a registered webhook")
@app_commands.describe(tournament_id="Tournament ID", url="Webhook URL to remove")
async def webhooks_remove(
    interaction: discord.Interaction,
    tournament_id: str,
    url: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.bot.helpers.formatters import success_embed, error_embed

    async with AsyncSessionLocal() as session:
        async with session.begin():
            _, tournament = await _require_admin_and_tournament(session, interaction, tournament_id)
            if not tournament:
                return

            config = dict(tournament.channel_config or {})
            webhooks = [wh for wh in config.get("webhooks", []) if wh.get("url") != url]
            if len(webhooks) == len(config.get("webhooks", [])):
                await interaction.followup.send(embed=error_embed("Webhook URL not found."), ephemeral=True)
                return
            config["webhooks"] = webhooks
            tournament.channel_config = config

    await interaction.followup.send(
        embed=success_embed(f"Webhook `{url[:60]}` removed from **{tournament.name}**.", title="Webhook Removed"),
        ephemeral=True,
    )


# ── /flags webhooks_list ──────────────────────────────────────────────────────

@flags_group.command(name="webhooks_list", description="List all registered webhooks")
@app_commands.describe(tournament_id="Tournament ID")
async def webhooks_list(
    interaction: discord.Interaction,
    tournament_id: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        _, tournament = await _require_admin_and_tournament(session, interaction, tournament_id)
        if not tournament:
            return
        webhooks: list[dict] = tournament.channel_config.get("webhooks", [])

    embed = discord.Embed(title=f"🔗 Webhooks — {tournament.name}", color=discord.Color.blurple())
    if not webhooks:
        embed.description = "No webhooks registered. Use `/flags webhooks_add` to add one."
    else:
        for wh in webhooks:
            has_secret = "🔐 Signed" if wh.get("secret") else "🔓 Unsigned"
            events = ", ".join(wh.get("events", ["*"]))
            embed.add_field(
                name=f"`{wh['url'][:60]}`",
                value=f"Events: `{events}` | {has_secret}",
                inline=False,
            )
    await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FlagsCog(bot))
