"""
Audit cog — view audit trail and snapshots from Discord.

Commands:
  /audit trail     — show recent audit events for a tournament
  /audit snapshot  — show available snapshots for a tournament
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging

from app.database.models.staff import StaffRole

logger = logging.getLogger(__name__)

audit_group = app_commands.Group(
    name="audit",
    description="View audit trail and snapshots (Staff only)",
)


class AuditCog(commands.Cog, name="audit"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.tree.add_command(audit_group)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(audit_group.name)


# ── Action icon map ───────────────────────────────────────────────────────────

_ACTION_ICONS: dict[str, str] = {
    "tournament.status_changed":  "🔄",
    "team.disqualified":          "🚫",
    "match.override_winner":      "⚔️",
    "bracket.advanced":           "⏩",
    "noshow.processed":           "👻",
    "registration.approved":      "✅",
    "registration.rejected":      "❌",
    "registration.flagged":       "🚩",
    "score.submitted":            "📝",
    "score.override":             "⚠️",
    "feature_flag.set":           "🚩",
    "webhook.added":              "🔗",
    "snapshot.created":           "📸",
}


def _action_icon(action: str) -> str:
    return _ACTION_ICONS.get(action, "📋")


# ── /audit trail ──────────────────────────────────────────────────────────────

@audit_group.command(name="trail", description="Show recent audit events for a tournament")
@app_commands.describe(
    tournament_id="Tournament ID",
    limit="Number of events to show (max 25, default 15)",
)
async def audit_trail(
    interaction: discord.Interaction,
    tournament_id: str,
    limit: int = 15,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.models.guild import Guild
    from app.database.repositories.tournament import TournamentRepository
    from app.database.repositories.audit import AuditRepository
    from app.bot.helpers.permissions import has_permission
    from app.bot.helpers.formatters import error_embed
    from sqlalchemy import select

    limit = min(max(limit, 1), 25)

    async with AsyncSessionLocal() as session:
        if not await has_permission(session, interaction.user, str(interaction.guild_id), StaffRole.JUDGE):
            await interaction.followup.send(embed=error_embed("Staff role required."), ephemeral=True)
            return

        guild_q = select(Guild).where(Guild.discord_guild_id == str(interaction.guild_id), Guild.deleted_at.is_(None))
        guild = (await session.execute(guild_q)).scalar_one_or_none()
        if not guild:
            await interaction.followup.send("Server not registered.", ephemeral=True)
            return

        t_repo = TournamentRepository(session)
        tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
        if not tournament:
            await interaction.followup.send(embed=error_embed("Tournament not found."), ephemeral=True)
            return

        audit_repo = AuditRepository(session)
        entries = await audit_repo.list_for_tournament(guild.organization_id, tournament.id, limit=limit)

    embed = discord.Embed(
        title=f"📜 Audit Trail — {tournament.name}",
        color=discord.Color.blurple(),
        description=f"Showing last **{len(entries)}** events (newest first)",
    )

    if not entries:
        embed.description = "No audit events recorded yet."
    else:
        lines = []
        for entry in entries:
            icon = _action_icon(entry.action)
            ts = f"<t:{int(entry.created_at.timestamp())}:R>" if entry.created_at else ""
            actor = f"`{entry.actor_type or 'system'}`"
            action_label = entry.action.replace(".", " › ").replace("_", " ").title()

            # Build a short detail string from payload
            detail = ""
            if entry.payload:
                if "old_status" in entry.payload and "new_status" in entry.payload:
                    detail = f" `{entry.payload['old_status']}` → `{entry.payload['new_status']}`"
                elif "reason" in entry.payload:
                    detail = f" — {entry.payload['reason'][:40]}"

            lines.append(f"{icon} {ts} **{action_label}**{detail} by {actor}")

        # Discord embed description limit ~4096
        embed.description = "\n".join(lines)

    embed.set_footer(text=f"Tournament ID: {tournament.id[:8]}")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /audit snapshot ──────────────────────────────────────────────────────────

@audit_group.command(name="snapshot", description="Show available snapshots for a tournament")
@app_commands.describe(tournament_id="Tournament ID")
async def audit_snapshot(
    interaction: discord.Interaction,
    tournament_id: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.models.guild import Guild
    from app.database.models.snapshot import TournamentSnapshot
    from app.database.repositories.tournament import TournamentRepository
    from app.bot.helpers.permissions import has_permission
    from app.bot.helpers.formatters import error_embed
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        if not await has_permission(session, interaction.user, str(interaction.guild_id), StaffRole.JUDGE):
            await interaction.followup.send(embed=error_embed("Staff role required."), ephemeral=True)
            return

        guild_q = select(Guild).where(Guild.discord_guild_id == str(interaction.guild_id), Guild.deleted_at.is_(None))
        guild = (await session.execute(guild_q)).scalar_one_or_none()
        if not guild:
            await interaction.followup.send("Server not registered.", ephemeral=True)
            return

        t_repo = TournamentRepository(session)
        tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
        if not tournament:
            await interaction.followup.send(embed=error_embed("Tournament not found."), ephemeral=True)
            return

        snap_q = (
            select(TournamentSnapshot)
            .where(TournamentSnapshot.tournament_id == tournament.id)
            .order_by(TournamentSnapshot.created_at.desc())
            .limit(10)
        )
        snapshots = (await session.execute(snap_q)).scalars().all()

    embed = discord.Embed(
        title=f"📸 Snapshots — {tournament.name}",
        color=discord.Color.blurple(),
    )

    if not snapshots:
        embed.description = "No snapshots recorded yet. Snapshots are taken automatically at key lifecycle events."
    else:
        for snap in snapshots:
            ts = f"<t:{int(snap.created_at.timestamp())}:F>" if snap.created_at else "unknown"
            embed.add_field(
                name=f"`{snap.id[:8]}` — {snap.label or 'snapshot'}",
                value=f"Taken: {ts}\nTrigger: `{snap.trigger or 'manual'}`",
                inline=False,
            )

    embed.set_footer(text=f"Use /override snapshot to take a manual snapshot.")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AuditCog(bot))
