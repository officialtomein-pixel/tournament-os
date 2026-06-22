"""
Override cog — manual staff interventions (Tournament Admin / Head Judge only).

Commands:
  /override dq          — disqualify a team
  /override match_winner — force-declare a match winner
  /override advance      — force-advance the bracket to the next round
  /override forfeit_noshows — immediately process check-in no-shows
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging

from app.database.models.staff import StaffRole

logger = logging.getLogger(__name__)

override_group = app_commands.Group(
    name="override",
    description="Manual staff interventions (Tournament Admin only)",
)


class OverrideCog(commands.Cog, name="override"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.tree.add_command(override_group)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(override_group.name)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _require_admin(session, interaction: discord.Interaction) -> tuple | None:
    """Return (guild, tournament_repo) if user has admin permissions, else None."""
    from app.database.models.guild import Guild
    from app.bot.helpers.permissions import has_permission
    from sqlalchemy import select

    if not await has_permission(
        session, interaction.user, str(interaction.guild_id), StaffRole.TOURNAMENT_ADMIN
    ):
        await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Permission Denied",
                description="This command requires **Tournament Admin** role.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return None

    guild_q = select(Guild).where(
        Guild.discord_guild_id == str(interaction.guild_id),
        Guild.deleted_at.is_(None),
    )
    guild = (await session.execute(guild_q)).scalar_one_or_none()
    if not guild:
        await interaction.followup.send("Server not registered.", ephemeral=True)
        return None

    return guild


# ── /override dq ──────────────────────────────────────────────────────────────

@override_group.command(name="dq", description="Disqualify a team from the tournament")
@app_commands.describe(
    tournament_id="Tournament ID (short or full UUID)",
    team_id="Team ID to disqualify",
    reason="Reason for DQ",
)
async def override_dq(
    interaction: discord.Interaction,
    tournament_id: str,
    team_id: str,
    reason: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.repositories.tournament import TournamentRepository
    from app.database.repositories.team import TeamRepository
    from app.database.repositories.audit import AuditRepository
    from app.database.repositories.user import UserRepository
    from app.bot.helpers.formatters import success_embed, error_embed

    async with AsyncSessionLocal() as session:
        async with session.begin():
            guild = await _require_admin(session, interaction)
            if not guild:
                return

            t_repo = TournamentRepository(session)
            tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
            if not tournament:
                await interaction.followup.send(embed=error_embed("Tournament not found."), ephemeral=True)
                return

            team_repo = TeamRepository(session)
            team = await team_repo.get_by_id(team_id, guild.organization_id, tournament.id)
            if not team:
                await interaction.followup.send(embed=error_embed("Team not found."), ephemeral=True)
                return

            user_repo = UserRepository(session)
            actor, _ = await user_repo.get_or_create(str(interaction.user.id), interaction.user.name)

            await team_repo.soft_delete(team.id, guild.organization_id, tournament.id)

            audit = AuditRepository(session)
            await audit.log(
                organization_id=guild.organization_id,
                tournament_id=tournament.id,
                action="team.disqualified",
                actor_id=actor.id,
                target_type="team",
                target_id=team.id,
                payload={"reason": reason},
            )

    await interaction.followup.send(
        embed=success_embed(
            f"Team **{team.name}** (`{team.id[:8]}`) has been disqualified.\n"
            f"Reason: {reason}",
            title="⚠️ Team DQ'd",
        ),
        ephemeral=True,
    )
    logger.info(
        "override.dq: team=%s tournament=%s by=%s reason=%r",
        team_id[:8], tournament_id[:8], interaction.user.id, reason,
    )


# ── /override match_winner ─────────────────────────────────────────────────────

@override_group.command(name="match_winner", description="Force-declare the winner of a match")
@app_commands.describe(
    match_id="Match ID",
    winner_team_id="Team ID of the winner",
    reason="Reason for the override",
)
async def override_match_winner(
    interaction: discord.Interaction,
    match_id: str,
    winner_team_id: str,
    reason: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.models.match import Match
    from app.database.repositories.user import UserRepository
    from app.services.match.score_handler import ScoreHandler
    from app.bot.helpers.formatters import success_embed, error_embed
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        async with session.begin():
            guild = await _require_admin(session, interaction)
            if not guild:
                return

            q = (
                select(Match)
                .where(Match.id == match_id)
                .where(Match.organization_id == guild.organization_id)
                .where(Match.deleted_at.is_(None))
            )
            match = (await session.execute(q)).scalar_one_or_none()
            if not match:
                await interaction.followup.send(embed=error_embed("Match not found."), ephemeral=True)
                return

            valid_ids = {match.team1_id, match.team2_id} - {None}
            if winner_team_id not in valid_ids:
                await interaction.followup.send(
                    embed=error_embed(
                        f"Winner ID must be one of the match teams: {', '.join(str(t)[:8] for t in valid_ids)}"
                    ),
                    ephemeral=True,
                )
                return

            loser_id = (
                match.team2_id if winner_team_id == match.team1_id else match.team1_id
            )

            user_repo = UserRepository(session)
            actor, _ = await user_repo.get_or_create(str(interaction.user.id), interaction.user.name)

            handler = ScoreHandler(session)
            await handler.submit_score(
                match_id=match_id,
                tournament_id=match.tournament_id,
                organization_id=guild.organization_id,
                submitted_by=actor.id,
                score_team1={"score": 1} if winner_team_id == match.team1_id else {"score": 0},
                score_team2={"score": 0} if winner_team_id == match.team1_id else {"score": 1},
                winner_id=winner_team_id,
                loser_id=loser_id,
                is_override=True,
                override_reason=reason,
            )

    await interaction.followup.send(
        embed=success_embed(
            f"Match `{match_id[:8]}` result overridden.\n"
            f"Winner: team `{winner_team_id[:8]}`\nReason: {reason}",
            title="✅ Match Winner Set",
        ),
        ephemeral=True,
    )
    logger.info(
        "override.match_winner: match=%s winner=%s by=%s",
        match_id[:8], winner_team_id[:8], interaction.user.id,
    )


# ── /override advance ──────────────────────────────────────────────────────────

@override_group.command(name="advance", description="Force-advance the bracket to the next round")
@app_commands.describe(tournament_id="Tournament ID")
async def override_advance(
    interaction: discord.Interaction,
    tournament_id: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.repositories.tournament import TournamentRepository
    from app.services.bracket.advancement import BracketAdvancement
    from app.bot.helpers.formatters import success_embed, error_embed

    async with AsyncSessionLocal() as session:
        async with session.begin():
            guild = await _require_admin(session, interaction)
            if not guild:
                return

            t_repo = TournamentRepository(session)
            tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
            if not tournament:
                await interaction.followup.send(embed=error_embed("Tournament not found."), ephemeral=True)
                return

            try:
                adv = BracketAdvancement(session)
                result = await adv.advance(guild.organization_id, tournament.id)
            except Exception as exc:
                await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
                return

    await interaction.followup.send(
        embed=success_embed(
            f"Bracket advanced for **{tournament.name}**.\n{result}",
            title="⏩ Bracket Advanced",
        ),
        ephemeral=True,
    )
    logger.info(
        "override.advance: tournament=%s by=%s", tournament_id[:8], interaction.user.id
    )


# ── /override forfeit_noshows ──────────────────────────────────────────────────

@override_group.command(
    name="forfeit_noshows",
    description="Immediately process check-in no-shows and remove non-checked-in teams",
)
@app_commands.describe(tournament_id="Tournament ID")
async def override_forfeit_noshows(
    interaction: discord.Interaction,
    tournament_id: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.repositories.tournament import TournamentRepository
    from app.services.checkin.noshow_handler import NoShowHandler
    from app.bot.helpers.formatters import success_embed, error_embed

    async with AsyncSessionLocal() as session:
        async with session.begin():
            guild = await _require_admin(session, interaction)
            if not guild:
                return

            t_repo = TournamentRepository(session)
            tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
            if not tournament:
                await interaction.followup.send(embed=error_embed("Tournament not found."), ephemeral=True)
                return

            try:
                handler = NoShowHandler(session)
                result = await handler.process_noshows(guild.organization_id, tournament.id)
            except Exception as exc:
                await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
                return

    removed = result.get("removed", [])
    promoted = result.get("promoted", [])

    await interaction.followup.send(
        embed=success_embed(
            f"No-show processing complete for **{tournament.name}**.\n"
            f"Teams removed: **{len(removed)}**\n"
            f"Reserves promoted: **{len(promoted)}**",
            title="✅ No-Shows Processed",
        ),
        ephemeral=True,
    )
    logger.info(
        "override.forfeit_noshows: tournament=%s removed=%d promoted=%d by=%s",
        tournament_id[:8], len(removed), len(promoted), interaction.user.id,
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OverrideCog(bot))
