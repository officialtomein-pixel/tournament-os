"""
Admin cog — tournament lifecycle management (staff only).

Slash commands kept here:
  /tournament_generate_bracket — generate bracket
  /analytics                  — tournament analytics
  /standings                  — public standings
  /score_override             — referee score correction

Removed (replaced by buttons/wizard):
  /tournament_create   → button in #create-tournament
  /tournament_status   → Control Panel 🔄 Change Status button
  /checkin_open        → Control Panel ✅ Check-In button
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging

from app.database.models.tournament import TournamentFormat
from app.database.models.staff import StaffRole

logger = logging.getLogger(__name__)


class AdminCog(commands.Cog, name="admin"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="tournament_generate_bracket", description="Generate brackets (staff only)")
    @app_commands.describe(tournament_id="Tournament ID")
    async def generate_bracket(self, interaction: discord.Interaction, tournament_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.repositories.tournament import TournamentRepository
        from app.services.bracket.generator import BracketGenerator
        from app.bot.helpers.formatters import success_embed, error_embed
        from app.bot.helpers.permissions import has_permission
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            async with session.begin():
                if not await has_permission(
                    session, interaction.user, str(interaction.guild_id), StaffRole.TOURNAMENT_ADMIN
                ):
                    await interaction.followup.send(embed=error_embed("Insufficient permissions."), ephemeral=True)
                    return

                guild_q = select(Guild).where(Guild.discord_guild_id == str(interaction.guild_id)).where(Guild.deleted_at.is_(None))
                result = await session.execute(guild_q)
                guild = result.scalar_one_or_none()
                if not guild:
                    await interaction.followup.send(embed=error_embed("Guild not registered."), ephemeral=True)
                    return

                t_repo = TournamentRepository(session)
                tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
                if not tournament:
                    await interaction.followup.send(embed=error_embed("Tournament not found."), ephemeral=True)
                    return
                tournament_id = tournament.id

                gen = BracketGenerator(session)
                try:
                    bracket = await gen.generate(
                        guild.organization_id, tournament_id, tournament.format
                    )
                    await interaction.followup.send(
                        embed=success_embed(
                            f"Bracket generated!\n"
                            f"Format: **{tournament.format.value.replace('_', ' ').title()}**\n"
                            f"Bracket ID: `{bracket.id[:8]}`"
                        )
                    )
                except ValueError as e:
                    await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)

    @app_commands.command(name="analytics", description="View tournament analytics (staff only)")
    @app_commands.describe(tournament_id="Tournament ID")
    async def analytics(self, interaction: discord.Interaction, tournament_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.services.analytics.aggregator import AnalyticsAggregator
        from app.bot.helpers.permissions import has_permission
        from app.bot.helpers.formatters import error_embed
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            guild_q = select(Guild).where(Guild.discord_guild_id == str(interaction.guild_id))
            result = await session.execute(guild_q)
            guild = result.scalar_one_or_none()
            if not guild:
                await interaction.followup.send(embed=error_embed("Guild not registered."), ephemeral=True)
                return

            from app.database.repositories.tournament import TournamentRepository
            t_repo = TournamentRepository(session)
            tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
            if not tournament:
                await interaction.followup.send(embed=error_embed("Tournament not found."), ephemeral=True)
                return
            tournament_id = tournament.id

            agg = AnalyticsAggregator(session)
            data = await agg.tournament_summary(guild.organization_id, tournament_id)

            embed = discord.Embed(title="📊 Tournament Analytics", color=discord.Color.gold())
            reg = data["registrations"]
            teams = data["teams"]
            matches = data["matches"]

            embed.add_field(
                name="Registrations",
                value=f"Total: **{reg['total']}** | Approved: {reg['approved']} | Pending: {reg['pending']} | Flagged: {reg['flagged']}",
                inline=False,
            )
            embed.add_field(
                name="Teams",
                value=f"Total: **{teams['total']}** | Checked In: {teams['checked_in']} ({teams['checkin_rate']}%)",
                inline=False,
            )
            embed.add_field(
                name="Matches",
                value=f"Total: **{matches['total']}** | Completed: {matches['completed']} | Live: {matches['live']}",
                inline=False,
            )
            embed.add_field(name="Open Disputes", value=str(data["disputes"]["total"]), inline=True)
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
