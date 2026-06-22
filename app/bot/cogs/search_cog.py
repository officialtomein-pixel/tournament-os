"""
Search cog — player-facing tournament search commands.

Commands:
  /search tournament  — find tournaments by name or game
  /search team        — find your team or search by name
  /search match       — look up a match by ID or round
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)

search_group = app_commands.Group(
    name="search",
    description="Search tournaments, teams, and matches",
)


class SearchCog(commands.Cog, name="search"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.tree.add_command(search_group)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(search_group.name)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_guild_org(session, guild_id: int) -> str | None:
    from app.database.models.guild import Guild
    from sqlalchemy import select

    q = select(Guild).where(
        Guild.discord_guild_id == str(guild_id),
        Guild.deleted_at.is_(None),
    )
    guild = (await session.execute(q)).scalar_one_or_none()
    return guild.organization_id if guild else None


# ── /search tournament ─────────────────────────────────────────────────────────

@search_group.command(name="tournament", description="Search tournaments by name or game")
@app_commands.describe(query="Name or game to search for")
async def search_tournament(interaction: discord.Interaction, query: str) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.models.tournament import Tournament
    from sqlalchemy import select, or_, func

    async with AsyncSessionLocal() as session:
        org_id = await _get_guild_org(session, interaction.guild_id)
        if not org_id:
            await interaction.followup.send("Server not registered.", ephemeral=True)
            return

        pattern = f"%{query.lower()}%"
        q = (
            select(Tournament)
            .where(
                Tournament.organization_id == org_id,
                Tournament.deleted_at.is_(None),
                or_(
                    func.lower(Tournament.name).like(pattern),
                    func.lower(Tournament.game).like(pattern),
                ),
            )
            .order_by(Tournament.created_at.desc())
            .limit(10)
        )
        results = list((await session.execute(q)).scalars().all())

    if not results:
        await interaction.followup.send(
            embed=discord.Embed(
                title="🔍 No Results",
                description=f"No tournaments found matching `{query}`.",
                color=discord.Color.light_grey(),
            ),
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"🔍 Tournaments matching '{query}'",
        color=discord.Color.blurple(),
    )
    for t in results:
        status_icon = {
            "registration_open": "📝",
            "checkin_open": "✅",
            "live": "🔴",
            "completed": "🏆",
        }.get(t.status.value if t.status else "", "📅")
        embed.add_field(
            name=f"{status_icon} {t.name}",
            value=(
                f"Game: **{t.game or 'N/A'}** | Format: **{t.format.value.replace('_',' ').title() if t.format else 'N/A'}**\n"
                f"Status: `{t.status.value if t.status else 'unknown'}` | ID: `{t.id[:8]}`"
            ),
            inline=False,
        )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /search team ───────────────────────────────────────────────────────────────

@search_group.command(name="team", description="Find your team or search by team name")
@app_commands.describe(
    tournament_id="Tournament ID",
    name="Team name to search (leave empty to find your own team)",
)
async def search_team(
    interaction: discord.Interaction,
    tournament_id: str,
    name: str | None = None,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.models.team import Team, TeamMember
    from app.database.models.user import User
    from app.database.repositories.tournament import TournamentRepository
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as session:
        org_id = await _get_guild_org(session, interaction.guild_id)
        if not org_id:
            await interaction.followup.send("Server not registered.", ephemeral=True)
            return

        t_repo = TournamentRepository(session)
        tournament = await t_repo.get_by_id(tournament_id, org_id)
        if not tournament:
            await interaction.followup.send("Tournament not found.", ephemeral=True)
            return

        if name:
            # Search by name
            pattern = f"%{name.lower()}%"
            q = (
                select(Team)
                .where(
                    Team.organization_id == org_id,
                    Team.tournament_id == tournament.id,
                    Team.deleted_at.is_(None),
                    func.lower(Team.name).like(pattern),
                )
                .limit(10)
            )
            teams = list((await session.execute(q)).scalars().all())
        else:
            # Find user's own team
            user_q = select(User).where(
                User.discord_user_id == str(interaction.user.id),
                User.deleted_at.is_(None),
            )
            user = (await session.execute(user_q)).scalar_one_or_none()
            if not user:
                await interaction.followup.send("You are not registered.", ephemeral=True)
                return

            member_q = (
                select(TeamMember)
                .where(
                    TeamMember.user_id == user.id,
                    TeamMember.tournament_id == tournament.id,
                    TeamMember.is_active.is_(True),
                )
            )
            member = (await session.execute(member_q)).scalar_one_or_none()
            if not member:
                await interaction.followup.send(
                    "You are not on any team in this tournament.", ephemeral=True
                )
                return
            t = await session.get(Team, member.team_id)
            teams = [t] if t else []

    if not teams:
        await interaction.followup.send(
            embed=discord.Embed(
                title="🔍 No Teams Found",
                description=f"No teams matched `{name}` in **{tournament.name}**.",
                color=discord.Color.light_grey(),
            ),
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"👥 Teams in {tournament.name}",
        color=discord.Color.blue(),
    )
    for team in teams:
        status_icon = "✅" if team.checkin_status == "checked_in" else "⏳"
        embed.add_field(
            name=f"{status_icon} {team.name}" + (f" [{team.tag}]" if team.tag else ""),
            value=(
                f"Check-in: `{team.checkin_status}` | Seed: `{team.seed or 'N/A'}`\n"
                f"ID: `{team.id[:8]}`"
            ),
            inline=False,
        )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /search match ──────────────────────────────────────────────────────────────

@search_group.command(name="match", description="Look up matches by tournament and round")
@app_commands.describe(
    tournament_id="Tournament ID",
    round_num="Round number (leave empty for all open matches)",
)
async def search_match(
    interaction: discord.Interaction,
    tournament_id: str,
    round_num: int | None = None,
) -> None:
    await interaction.response.defer(ephemeral=True)
    from app.database.session import AsyncSessionLocal
    from app.database.models.match import Match, MatchStatus
    from app.database.models.team import Team
    from app.database.repositories.tournament import TournamentRepository
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        org_id = await _get_guild_org(session, interaction.guild_id)
        if not org_id:
            await interaction.followup.send("Server not registered.", ephemeral=True)
            return

        t_repo = TournamentRepository(session)
        tournament = await t_repo.get_by_id(tournament_id, org_id)
        if not tournament:
            await interaction.followup.send("Tournament not found.", ephemeral=True)
            return

        q = (
            select(Match)
            .where(
                Match.organization_id == org_id,
                Match.tournament_id == tournament.id,
                Match.deleted_at.is_(None),
            )
            .order_by(Match.round, Match.created_at)
            .limit(15)
        )
        if round_num is not None:
            q = q.where(Match.round == round_num)
        else:
            # Default: only in-progress or pending matches
            q = q.where(Match.status.in_(["pending", "in_progress", "protested"]))

        matches = list((await session.execute(q)).scalars().all())

        # Batch-load team names
        team_ids = {m.team1_id for m in matches} | {m.team2_id for m in matches}
        team_ids.discard(None)
        team_map: dict[str, str] = {}
        if team_ids:
            t_q = select(Team).where(Team.id.in_(team_ids))
            for team in (await session.execute(t_q)).scalars().all():
                team_map[team.id] = team.name

    if not matches:
        desc = f"Round {round_num}" if round_num else "any open round"
        await interaction.followup.send(
            embed=discord.Embed(
                title="🔍 No Matches Found",
                description=f"No matches in {desc} for **{tournament.name}**.",
                color=discord.Color.light_grey(),
            ),
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"🎮 Matches — {tournament.name}",
        color=discord.Color.blue(),
    )
    status_icons = {
        "pending": "⏳",
        "in_progress": "🔴",
        "completed": "✅",
        "protested": "⚠️",
        "bye": "⏭️",
        "cancelled": "❌",
    }
    for m in matches:
        t1 = team_map.get(m.team1_id or "", "TBD")
        t2 = team_map.get(m.team2_id or "", "TBD")
        icon = status_icons.get(m.status.value if m.status else "pending", "❓")
        score = ""
        if m.score_team1 and m.score_team2:
            s1 = m.score_team1.get("score", "?")
            s2 = m.score_team2.get("score", "?")
            score = f" | **{s1} – {s2}**"
        embed.add_field(
            name=f"{icon} R{m.round}: {t1} vs {t2}{score}",
            value=f"Status: `{m.status.value if m.status else 'pending'}` | ID: `{m.id[:8]}`",
            inline=False,
        )
    await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SearchCog(bot))
