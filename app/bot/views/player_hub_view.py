"""
Persistent Player Hub view — posted in 📝-register channel during server setup.

Buttons:
  [📝 Register]           → find open tournament → open RegistrationModal
  [📋 My Status]          → ephemeral registration status
  [📅 View Schedule]      → ephemeral schedule info
  [🏆 View Prize Pool]    → ephemeral prize pool info

custom_ids are static strings (no encoded IDs) because this view is not
tied to a specific tournament — it queries the DB at click time.
"""
import logging

import discord

logger = logging.getLogger(__name__)


class PlayerHubView(discord.ui.View):
    """Persistent view — survives bot restarts."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    # ── Register ──────────────────────────────────────────────────────────────

    @discord.ui.button(
        label="Register",
        style=discord.ButtonStyle.success,
        emoji="📝",
        custom_id="player_hub:register",
    )
    async def register(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.models.tournament import Tournament, TournamentStatus
        from app.database.repositories.tournament import TournamentRepository
        from app.services.registration.form_builder import FormBuilderService
        from app.bot.views.registration_modal import RegistrationModal
        from app.bot.helpers.formatters import error_embed
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            guild_q = select(Guild).where(
                Guild.discord_guild_id == str(interaction.guild_id),
                Guild.deleted_at.is_(None),
            )
            guild = (await session.execute(guild_q)).scalar_one_or_none()
            if not guild:
                await interaction.response.send_message(
                    embed=error_embed("This server is not registered. Ask an admin to run `/setup tournament`."),
                    ephemeral=True,
                )
                return

            open_q = (
                select(Tournament)
                .where(Tournament.organization_id == guild.organization_id)
                .where(Tournament.status == TournamentStatus.REGISTRATION_OPEN)
                .where(Tournament.deleted_at.is_(None))
            )
            tournaments = (await session.execute(open_q)).scalars().all()

            if not tournaments:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="📝 Registration",
                        description="There are no tournaments open for registration right now.\nCheck back when registration opens!",
                        color=discord.Color.yellow(),
                    ),
                    ephemeral=True,
                )
                return

            if len(tournaments) == 1:
                t = tournaments[0]
                fb = FormBuilderService(session)
                form = await fb.get_active_form(guild.organization_id, t.id)
                fields: list[dict] = []
                if form and form.fields:
                    fields = [
                        {
                            "field_key": f.field_key,
                            "label": f.label,
                            "is_required": f.is_required,
                            "long_text": f.field_type.value == "long_text",
                            "placeholder": "",
                        }
                        for f in form.fields
                    ]
                else:
                    fields = [{"field_key": "in_game_name", "label": "In-Game Name", "is_required": True, "long_text": False, "placeholder": "Your IGN"}]
                modal = RegistrationModal(tournament_id=t.id, organization_id=guild.organization_id, fields=fields[:5])
                await interaction.response.send_modal(modal)
                return

            # Multiple open tournaments → let user pick
            options = [
                discord.SelectOption(label=t.name[:100], value=t.id, description=f"{t.game} · {t.format.value.replace('_',' ').title()}")
                for t in tournaments[:25]
            ]
            view = _TournamentPickView(options=options, org_id=guild.organization_id)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="📝 Choose Tournament",
                    description="Multiple tournaments are open. Select one to register:",
                    color=discord.Color.blurple(),
                ),
                view=view,
                ephemeral=True,
            )

    # ── My Status ─────────────────────────────────────────────────────────────

    @discord.ui.button(
        label="My Status",
        style=discord.ButtonStyle.secondary,
        emoji="📋",
        custom_id="player_hub:status",
    )
    async def my_status(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.models.registration import Registration
        from app.database.models.user import User
        from app.bot.helpers.formatters import error_embed
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            guild_q = select(Guild).where(
                Guild.discord_guild_id == str(interaction.guild_id),
                Guild.deleted_at.is_(None),
            )
            guild = (await session.execute(guild_q)).scalar_one_or_none()
            if not guild:
                await interaction.followup.send(embed=error_embed("Server not registered."), ephemeral=True)
                return

            q = (
                select(Registration)
                .join(User, Registration.submitted_by == User.id)
                .where(Registration.organization_id == guild.organization_id)
                .where(User.discord_user_id == str(interaction.user.id))
                .where(Registration.deleted_at.is_(None))
                .order_by(Registration.created_at.desc())
            )
            regs = (await session.execute(q)).scalars().all()

        if not regs:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="📋 Your Registrations",
                    description="You haven't registered for any tournaments yet.",
                    color=discord.Color.greyple(),
                ),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="📋 Your Registrations", color=discord.Color.blurple())
        status_emoji = {
            "pending": "⏳", "auto_approved": "✅", "manually_approved": "✅",
            "rejected": "❌", "flagged": "🚩", "hold": "⏸", "changes_requested": "🔄",
        }
        for reg in regs[:5]:
            emoji = status_emoji.get(reg.status.value, "❓")
            embed.add_field(
                name=f"{emoji} {reg.status.value.replace('_', ' ').title()}",
                value=f"ID: `{reg.id[:8]}` • Submitted: {reg.created_at.strftime('%b %d, %Y')}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── View Schedule ─────────────────────────────────────────────────────────

    @discord.ui.button(
        label="Schedule",
        style=discord.ButtonStyle.secondary,
        emoji="📅",
        custom_id="player_hub:schedule",
    )
    async def view_schedule(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.models.tournament import Tournament, TournamentStatus
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            guild_q = select(Guild).where(
                Guild.discord_guild_id == str(interaction.guild_id),
                Guild.deleted_at.is_(None),
            )
            guild = (await session.execute(guild_q)).scalar_one_or_none()
            if not guild:
                await interaction.followup.send("Server not registered.", ephemeral=True)
                return

            active_q = (
                select(Tournament)
                .where(Tournament.organization_id == guild.organization_id)
                .where(Tournament.status.in_([
                    TournamentStatus.REGISTRATION_OPEN,
                    TournamentStatus.CHECKIN_OPEN,
                    TournamentStatus.LIVE,
                    TournamentStatus.SCHEDULED,
                ]))
                .where(Tournament.deleted_at.is_(None))
            )
            tournaments = (await session.execute(active_q)).scalars().all()

        if not tournaments:
            await interaction.followup.send(
                embed=discord.Embed(title="📅 Schedule", description="No active tournaments at this time.", color=discord.Color.greyple()),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="📅 Tournament Schedule", color=discord.Color.blue())
        status_emoji = {"registration_open": "📝", "checkin_open": "✅", "live": "🔴", "scheduled": "📅"}
        for t in tournaments[:5]:
            emoji = status_emoji.get(t.status.value, "⚙️")
            val = f"**Game:** {t.game}\n**Format:** {t.format.value.replace('_', ' ').title()}\n**Status:** {emoji} {t.status.value.replace('_', ' ').title()}"
            if t.match_start_at:
                val += f"\n**Starts:** {t.match_start_at.strftime('%b %d, %Y %I:%M %p')}"
            embed.add_field(name=t.name, value=val, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── View Prize Pool ───────────────────────────────────────────────────────

    @discord.ui.button(
        label="Prize Pool",
        style=discord.ButtonStyle.secondary,
        emoji="🏆",
        custom_id="player_hub:prize",
    )
    async def view_prize(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.database.models.tournament import Tournament, TournamentStatus
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            guild_q = select(Guild).where(
                Guild.discord_guild_id == str(interaction.guild_id),
                Guild.deleted_at.is_(None),
            )
            guild = (await session.execute(guild_q)).scalar_one_or_none()
            if not guild:
                await interaction.followup.send("Server not registered.", ephemeral=True)
                return

            q = (
                select(Tournament)
                .where(Tournament.organization_id == guild.organization_id)
                .where(Tournament.status.notin_([TournamentStatus.CANCELLED, TournamentStatus.ARCHIVED]))
                .where(Tournament.deleted_at.is_(None))
            )
            tournaments = (await session.execute(q)).scalars().all()

        embed = discord.Embed(title="🏆 Prize Pools", color=discord.Color.gold())
        for t in tournaments[:5]:
            embed.add_field(
                name=t.name,
                value=t.prize_pool or "Prize pool to be announced.",
                inline=False,
            )
        if not tournaments:
            embed.description = "No tournament information available."
        await interaction.followup.send(embed=embed, ephemeral=True)


class _TournamentPickView(discord.ui.View):
    """Ephemeral select when multiple tournaments are open."""

    def __init__(self, options: list[discord.SelectOption], org_id: str) -> None:
        super().__init__(timeout=60)
        self.org_id = org_id
        sel = discord.ui.Select(placeholder="Select a tournament…", options=options)
        sel.callback = self._on_pick
        self.add_item(sel)

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        tournament_id = interaction.data["values"][0]
        from app.database.session import AsyncSessionLocal
        from app.services.registration.form_builder import FormBuilderService
        from app.bot.views.registration_modal import RegistrationModal

        async with AsyncSessionLocal() as session:
            fb = FormBuilderService(session)
            form = await fb.get_active_form(self.org_id, tournament_id)
            fields: list[dict] = []
            if form and form.fields:
                fields = [
                    {"field_key": f.field_key, "label": f.label, "is_required": f.is_required,
                     "long_text": f.field_type.value == "long_text", "placeholder": ""}
                    for f in form.fields
                ]
            else:
                fields = [{"field_key": "in_game_name", "label": "In-Game Name", "is_required": True, "long_text": False, "placeholder": "Your IGN"}]

        await interaction.response.send_modal(
            RegistrationModal(tournament_id=tournament_id, organization_id=self.org_id, fields=fields[:5])
        )
