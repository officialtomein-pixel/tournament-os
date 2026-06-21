"""
Dispute management cog — staff commands to assign, resolve, and view disputes.
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)


class DisputeCog(commands.Cog, name="dispute"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="dispute_list", description="List open disputes (staff only)")
    @app_commands.describe(tournament_id="Tournament ID")
    async def dispute_list(self, interaction: discord.Interaction, tournament_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.models.staff import StaffRole
        from app.database.repositories.dispute import DisputeRepository
        from app.bot.helpers.formatters import error_embed
        from app.bot.helpers.permissions import has_permission
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            guild_q = select(Guild).where(Guild.discord_guild_id == str(interaction.guild_id))
            result = await session.execute(guild_q)
            guild = result.scalar_one_or_none()
            if not guild:
                await interaction.followup.send(embed=error_embed("Guild not registered."), ephemeral=True)
                return

            if not await has_permission(session, interaction.user, str(interaction.guild_id), StaffRole.MODERATOR):
                await interaction.followup.send(embed=error_embed("Insufficient permissions."), ephemeral=True)
                return

            from app.database.repositories.tournament import TournamentRepository
            t_repo = TournamentRepository(session)
            tournament = await t_repo.get_by_id(tournament_id, guild.organization_id)
            if not tournament:
                await interaction.followup.send(embed=error_embed("Tournament not found."), ephemeral=True)
                return
            tournament_id = tournament.id  # normalize short ID to full UUID

            repo = DisputeRepository(session)
            disputes = await repo.list_open(guild.organization_id, tournament_id)

            embed = discord.Embed(
                title=f"Open Disputes ({len(disputes)})",
                color=discord.Color.orange(),
            )
            for d in disputes[:10]:
                desc_preview = d.description[:80] + ("..." if len(d.description) > 80 else "")
                embed.add_field(
                    name=f"`{d.id[:8]}` — {d.case_type.value.replace('_', ' ').title()}",
                    value=f"Status: {d.status.value} | {desc_preview}",
                    inline=False,
                )
            if not disputes:
                embed.description = "No open disputes 🎉"
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="dispute_resolve", description="Resolve a dispute (staff only)")
    @app_commands.describe(dispute_id="Dispute ID (first 8 chars)", resolution="Resolution notes")
    async def dispute_resolve(
        self, interaction: discord.Interaction, dispute_id: str, resolution: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.models.staff import StaffRole
        from app.database.models.dispute import Dispute, DisputeStatus
        from app.database.repositories.user import UserRepository
        from app.services.dispute.case_manager import DisputeCaseManager
        from app.bot.helpers.formatters import success_embed, error_embed
        from app.bot.helpers.permissions import has_permission
        from sqlalchemy import select, cast, String

        async with AsyncSessionLocal() as session:
            async with session.begin():
                guild_q = select(Guild).where(Guild.discord_guild_id == str(interaction.guild_id))
                result = await session.execute(guild_q)
                guild = result.scalar_one_or_none()
                if not guild:
                    await interaction.followup.send(embed=error_embed("Guild not registered."), ephemeral=True)
                    return

                if not await has_permission(session, interaction.user, str(interaction.guild_id), StaffRole.MODERATOR):
                    await interaction.followup.send(embed=error_embed("Insufficient permissions."), ephemeral=True)
                    return

                # Find dispute by ID prefix — cast UUID to text for LIKE search
                q = (
                    select(Dispute)
                    .where(cast(Dispute.id, String).like(f"{dispute_id}%"))
                    .where(Dispute.deleted_at.is_(None))
                    .where(Dispute.organization_id == guild.organization_id)
                )
                result2 = await session.execute(q)
                dispute = result2.scalar_one_or_none()
                if not dispute:
                    await interaction.followup.send(embed=error_embed(f"Dispute `{dispute_id}` not found."), ephemeral=True)
                    return

                user_repo = UserRepository(session)
                user, _ = await user_repo.get_or_create(str(interaction.user.id), interaction.user.name)

                svc = DisputeCaseManager(session)
                await svc.resolve(
                    dispute_id=dispute.id,
                    organization_id=guild.organization_id,
                    tournament_id=dispute.tournament_id,
                    resolved_by=user.id,
                    resolution=resolution,
                )
                await interaction.followup.send(
                    embed=success_embed(f"Dispute `{dispute_id}` resolved.", title="Dispute Resolved"),
                    ephemeral=True,
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DisputeCog(bot))
