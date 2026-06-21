"""
AI assistant cog — /ask, /setup tournament, /setup_server (legacy).
"""
import re
import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-")[:100] or "org"


class AIAssistantCog(commands.Cog, name="ai_assistant"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /ask ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="ask", description="Ask the AI tournament assistant a question")
    @app_commands.describe(
        question="Your question about the tournament",
        tournament_id="Tournament ID (optional — for tournament-specific questions)",
    )
    async def ask(
        self,
        interaction: discord.Interaction,
        question: str,
        tournament_id: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.repositories.user import UserRepository
        from app.ai.assistant.agent import TournamentAIAgent
        from app.bot.helpers.formatters import error_embed
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            async with session.begin():
                guild_q = select(Guild).where(Guild.discord_guild_id == str(interaction.guild_id))
                result = await session.execute(guild_q)
                guild = result.scalar_one_or_none()
                if not guild:
                    await interaction.followup.send(embed=error_embed("Server not registered."), ephemeral=True)
                    return

                user_repo = UserRepository(session)
                user, _ = await user_repo.get_or_create(str(interaction.user.id), interaction.user.name)

                agent = TournamentAIAgent(session)
                thread_id = str(interaction.channel_id) if interaction.channel else None
                try:
                    import asyncio
                    response = await asyncio.wait_for(
                        agent.chat(
                            organization_id=guild.organization_id,
                            guild_id=guild.id,
                            tournament_id=tournament_id,
                            user_id=user.id,
                            discord_user_id=str(interaction.user.id),
                            message=question,
                            thread_id=thread_id,
                        ),
                        timeout=60,
                    )

                    embed = discord.Embed(
                        title="🤖 AI Assistant",
                        description=response["reply"][:4096],
                        color=discord.Color.blurple(),
                    )
                    if response.get("escalated"):
                        embed.set_footer(text=f"Escalated to staff | Ticket: {(response.get('dispute_id') or '')[:8]}")
                        embed.color = discord.Color.orange()

                    await interaction.followup.send(embed=embed, ephemeral=True)

                except asyncio.TimeoutError:
                    logger.warning("AI /ask timed out for user %s", interaction.user.id)
                    await interaction.followup.send(
                        embed=error_embed("⏱️ The AI assistant took too long to respond (>60s). Please try again."),
                        ephemeral=True,
                    )
                except Exception as e:
                    logger.error("AI ask error: %s", e, exc_info=True)
                    await interaction.followup.send(
                        embed=error_embed("AI assistant encountered an error. Please try again."),
                        ephemeral=True,
                    )

    # ── /setup group ──────────────────────────────────────────────────────────

    setup_group = app_commands.Group(
        name="setup",
        description="Tournament OS setup commands",
    )

    @setup_group.command(
        name="tournament",
        description="Launch the interactive setup wizard (creates roles, channels, and server structure)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_tournament(self, interaction: discord.Interaction) -> None:
        """7-step interactive setup wizard — replaces the legacy /setup_server command."""
        if not interaction.guild:
            await interaction.response.send_message("Must be used inside a server.", ephemeral=True)
            return

        # Check if already set up
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.models.organization import Organization
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            g_q = select(Guild).where(
                Guild.discord_guild_id == str(interaction.guild_id),
                Guild.deleted_at.is_(None),
            )
            existing = (await session.execute(g_q)).scalar_one_or_none()
            if existing:
                org_q = select(Organization).where(Organization.id == existing.organization_id)
                org = (await session.execute(org_q)).scalar_one_or_none()
                name_display = org.name if org else existing.organization_id
                settings = dict(existing.settings or {})
                create_t_id = settings.get("channel_ids", {}).get("create_tournament") or settings.get("create_tournament_channel_id")
                ch_mention = f" (<#{create_t_id}>)" if create_t_id else ""
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="⚠️ Already Set Up",
                        description=(
                            f"This server is already configured as **{name_display}**.\n\n"
                            f"To create a tournament, go to the **Create Tournament** channel{ch_mention} and click the button.\n\n"
                            f"Org ID: `{existing.organization_id[:8]}`"
                        ),
                        color=discord.Color.yellow(),
                    ),
                    ephemeral=True,
                )
                return

        from app.bot.views.setup_wizard import SetupStep1Modal
        await interaction.response.send_modal(SetupStep1Modal())

    @setup_tournament.error
    async def setup_tournament_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission to run this command.", ephemeral=True
            )
        else:
            logger.error("setup tournament error: %s", error, exc_info=True)
            await interaction.response.send_message("An unexpected error occurred.", ephemeral=True)

    # ── /setup_server removed — use /setup tournament ─────────────────────────
    # This legacy command has been removed. The full interactive wizard
    # (/setup tournament) replaces it with roles, channels, and more.

    async def _setup_server_removed(self) -> None:
        """Legacy /setup_server has been removed. Use /setup tournament instead."""


async def setup(bot: commands.Bot) -> None:
    cog = AIAssistantCog(bot)
    await bot.add_cog(cog)
