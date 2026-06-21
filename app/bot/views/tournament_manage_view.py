"""
Persistent tournament management view posted in each tournament's private channel.

Contains a "Change Status" button that opens an ephemeral dropdown with valid
next-status options. After a status change, Discord side effects are applied:
  - registration_open  → create #registration channel with Register button
  - checkin_open       → post check-in button in registration channel
  - live               → make tournament category visible to @everyone
  - completed/cancelled → lock tournament channels

custom_id format: "manage_t:<tournament_id>:<org_id>"
Max length: 8+1+36+1+36 = 82 chars.
"""
import logging

import discord

from app.database.models.tournament import TournamentStatus, VALID_TRANSITIONS

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = "manage_t"

_STATUS_EMOJI = {
    "draft": "📝",
    "hidden": "👁️",
    "testing": "🧪",
    "scheduled": "📅",
    "registration_open": "📋",
    "registration_closed": "🔒",
    "checkin_open": "✅",
    "checkin_closed": "🔒",
    "live": "🔴",
    "under_review": "🔍",
    "completed": "🏆",
    "archived": "📦",
    "cancelled": "❌",
}


def _make_custom_id(tournament_id: str, org_id: str) -> str:
    return f"{CUSTOM_ID_PREFIX}:{tournament_id}:{org_id}"


def _parse_custom_id(custom_id: str) -> tuple[str, str] | None:
    parts = custom_id.split(":", 2)
    if len(parts) != 3 or parts[0] != CUSTOM_ID_PREFIX:
        return None
    return parts[1], parts[2]


def _channel_name(text: str, max_len: int = 90) -> str:
    import re
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len] or "channel"


def _make_manage_message(t_name: str, t_id: str, status: str, fmt: str) -> discord.Embed:
    emoji = _STATUS_EMOJI.get(status, "⚙️")
    embed = discord.Embed(
        title=f"⚙️ {t_name} — Management",
        color={
            "live": discord.Color.red(),
            "registration_open": discord.Color.green(),
            "completed": discord.Color.gold(),
            "cancelled": discord.Color.dark_red(),
        }.get(status, discord.Color.blurple()),
    )
    embed.add_field(name="Status", value=f"{emoji} {status.replace('_', ' ').title()}", inline=True)
    embed.add_field(name="Format", value=fmt.replace("_", " ").title(), inline=True)
    embed.add_field(name="ID", value=f"`{t_id[:8]}`", inline=True)
    embed.set_footer(text="Use the button below to change the tournament status.")
    return embed


class TournamentManageView(discord.ui.View):
    """Persistent view — survives bot restarts."""

    def __init__(self, tournament_id: str, org_id: str):
        super().__init__(timeout=None)
        self.tournament_id = tournament_id
        self.org_id = org_id

        btn = discord.ui.Button(
            label="Change Status",
            style=discord.ButtonStyle.primary,
            emoji="📊",
            custom_id=_make_custom_id(tournament_id, org_id),
        )
        btn.callback = self._open_status_picker
        self.add_item(btn)

    async def _open_status_picker(self, interaction: discord.Interaction) -> None:
        raw = interaction.data.get("custom_id", "")
        parsed = _parse_custom_id(raw)
        if not parsed:
            await interaction.response.send_message("Invalid button. Contact admin.", ephemeral=True)
            return
        tournament_id, org_id = parsed

        from app.database.session import AsyncSessionLocal
        from app.database.repositories.tournament import TournamentRepository
        from app.bot.helpers.formatters import error_embed

        async with AsyncSessionLocal() as session:
            t_repo = TournamentRepository(session)
            tournament = await t_repo.get_by_id(tournament_id, org_id)
            if not tournament:
                await interaction.response.send_message(embed=error_embed("Tournament not found."), ephemeral=True)
                return
            current_status = tournament.status
            t_name = tournament.name
            t_fmt = tournament.format.value
            t_game = tournament.game
            t_settings: dict = dict(tournament.channel_config or {})

        valid_nexts = VALID_TRANSITIONS.get(current_status, [])
        if not valid_nexts:
            await interaction.response.send_message(
                embed=error_embed(f"No valid transitions from **{current_status.value}**."),
                ephemeral=True,
            )
            return

        options = [
            discord.SelectOption(
                label=s.value.replace("_", " ").title(),
                value=s.value,
                emoji=_STATUS_EMOJI.get(s.value, "⚙️"),
            )
            for s in valid_nexts
        ]

        select_view = _StatusSelectView(
            tournament_id=tournament_id,
            org_id=org_id,
            t_name=t_name,
            t_fmt=t_fmt,
            t_game=t_game,
            t_settings=t_settings,
            options=options,
            bot=interaction.client,
        )

        embed = discord.Embed(
            title="📊 Change Tournament Status",
            description=f"**{t_name}** is currently **{current_status.value.replace('_', ' ').title()}**.\nSelect a new status below.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=select_view, ephemeral=True)


class _StatusSelectView(discord.ui.View):
    """Ephemeral select menu — not persistent (timeout=120s)."""

    def __init__(
        self,
        tournament_id: str,
        org_id: str,
        t_name: str,
        t_fmt: str,
        t_game: str = "",
        t_settings: dict | None = None,
        options: list[discord.SelectOption] | None = None,
        bot: discord.Client | None = None,
    ):
        super().__init__(timeout=120)
        self.tournament_id = tournament_id
        self.org_id = org_id
        self.t_name = t_name
        self.t_fmt = t_fmt
        self.t_game = t_game
        self.t_settings = t_settings or {}
        self.bot = bot

        select = discord.ui.Select(
            placeholder="Select new status…",
            options=options or [],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        new_status_value: str = interaction.data["values"][0]

        from app.database.session import AsyncSessionLocal
        from app.database.models.tournament import TournamentStatus
        from app.database.repositories.user import UserRepository
        from app.services.tournament.lifecycle import TournamentLifecycleService
        from app.bot.helpers.formatters import error_embed

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    user_repo = UserRepository(session)
                    user, _ = await user_repo.get_or_create(str(interaction.user.id), interaction.user.name)
                    svc = TournamentLifecycleService(session)
                    updated = await svc.transition_status(
                        tournament_id=self.tournament_id,
                        organization_id=self.org_id,
                        new_status=TournamentStatus(new_status_value),
                        actor_id=user.id,
                    )
                    new_status = updated.status

            # ── Discord side effects ───────────────────────────────────
            await self._handle_discord_effects(interaction, new_status, new_status_value)

            # ── Update management channel message ──────────────────────
            await self._update_manage_channel(interaction, new_status_value)

            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ Status Updated",
                    description=f"**{self.t_name}** is now **{new_status_value.replace('_', ' ').title()}**.",
                    color=discord.Color.green(),
                ),
                ephemeral=True,
            )

        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
        except Exception as exc:
            logger.exception("Status change error: %s", exc)
            from app.bot.helpers.formatters import error_embed as ef
            await interaction.followup.send(embed=ef("An unexpected error occurred."), ephemeral=True)

    async def _handle_discord_effects(
        self,
        interaction: discord.Interaction,
        new_status: TournamentStatus,
        new_status_value: str,
    ) -> None:
        guild = interaction.guild
        if not guild:
            return

        if new_status == TournamentStatus.REGISTRATION_OPEN:
            await self._open_registration_channel(guild)

        elif new_status == TournamentStatus.CHECKIN_OPEN:
            await self._post_checkin_button(guild, interaction.client)

        elif new_status == TournamentStatus.LIVE:
            await self._make_tournament_channels_visible(guild)

        elif new_status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED):
            await self._lock_tournament_channels(guild, new_status_value)

    async def _open_registration_channel(self, guild: discord.Guild) -> None:
        """Create a public #registration channel with a Register button."""
        from app.database.session import AsyncSessionLocal
        from app.database.models.guild import Guild
        from app.bot.views.registration_button_view import RegistrationButtonView, _make_custom_id as _reg_cid
        from sqlalchemy import select, update as sa_update
        from app.database.models.tournament import Tournament

        try:
            async with AsyncSessionLocal() as session:
                guild_q = select(Guild).where(Guild.discord_guild_id == str(guild.id))
                result = await session.execute(guild_q)
                guild_record = result.scalar_one_or_none()
                if not guild_record:
                    return
                guild_settings: dict = dict(guild_record.settings or {})

            reg_cat_id = guild_settings.get("registration_category_id")
            reg_cat: discord.CategoryChannel | None = None
            if reg_cat_id:
                ch = guild.get_channel(int(reg_cat_id))
                if isinstance(ch, discord.CategoryChannel):
                    reg_cat = ch

            # Create registration category on demand if missing
            if not reg_cat:
                reg_cat = await guild.create_category(
                    name="📝 Registrations",
                    reason="Tournament OS: registration category",
                )
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        g_row = await session.get(Guild, guild_record.id)
                        if g_row:
                            settings = dict(g_row.settings or {})
                            settings["registration_category_id"] = str(reg_cat.id)
                            g_row.settings = settings
                            await session.flush()

            # Create the channel
            reg_channel = await guild.create_text_channel(
                name=f"📋-{_channel_name(self.t_name)}-registration",
                category=reg_cat,
                topic=f"Register for {self.t_name} — click the button below!",
                reason=f"Tournament OS: registration for {self.t_name}",
            )

            # Post the Register button
            reg_view = RegistrationButtonView(tournament_id=self.tournament_id, org_id=self.org_id)
            self.bot.add_view(reg_view)

            game_label = self.t_game or self.t_fmt.replace("_", " ").title()
            reg_embed = discord.Embed(
                title=f"📋 {self.t_name} — Registration Open!",
                description=(
                    f"Registration for **{self.t_name}** is now open!\n\n"
                    f"**Game:** {game_label}\n"
                    f"**Format:** {self.t_fmt.replace('_', ' ').title()}\n\n"
                    "Click the button below to register. You'll be asked to fill in a short form."
                ),
                color=discord.Color.green(),
            )
            reg_embed.set_footer(text=f"Tournament ID: {self.tournament_id[:8]}")
            await reg_channel.send(embed=reg_embed, view=reg_view)

            # Save registration channel ID in tournament settings
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    t_row = await session.get(Tournament, self.tournament_id)
                    if t_row:
                        t_settings = dict(t_row.channel_config or {})
                        t_settings["registration_channel_id"] = str(reg_channel.id)
                        t_row.channel_config = t_settings
                        await session.flush()

            logger.info("Created registration channel %s for tournament %s", reg_channel.id, self.tournament_id)

        except Exception as exc:
            logger.error("Failed to create registration channel: %s", exc, exc_info=True)

    async def _post_checkin_button(self, guild: discord.Guild, bot: discord.Client) -> None:
        """Post check-in button in the registration channel."""
        from app.bot.views.checkin_button import CheckInView
        from app.database.session import AsyncSessionLocal
        from app.database.models.tournament import Tournament

        try:
            t_settings = self.t_settings
            reg_ch_id = t_settings.get("registration_channel_id")
            if not reg_ch_id:
                return

            ch = guild.get_channel(int(reg_ch_id))
            if not ch or not isinstance(ch, discord.TextChannel):
                return

            view = CheckInView(tournament_id=self.tournament_id, organization_id=self.org_id)
            bot.add_view(view)

            checkin_embed = discord.Embed(
                title=f"✅ {self.t_name} — Check-In Open!",
                description="Check-in is now open! Click the button below to confirm your participation.",
                color=discord.Color.green(),
            )
            await ch.send(embed=checkin_embed, view=view)

            # Update local t_settings so subsequent calls have it
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    from app.database.models.tournament import Tournament
                    t_row = await session.get(Tournament, self.tournament_id)
                    if t_row:
                        self.t_settings = dict(t_row.channel_config or {})

        except Exception as exc:
            logger.error("Failed to post check-in button: %s", exc, exc_info=True)

    async def _make_tournament_channels_visible(self, guild: discord.Guild) -> None:
        """Open the tournament channels category to @everyone when tournament goes LIVE."""
        try:
            t_cat_id = self.t_settings.get("tournament_category_id")
            if not t_cat_id:
                return
            t_cat = guild.get_channel(int(t_cat_id))
            if not isinstance(t_cat, discord.CategoryChannel):
                return

            everyone = guild.default_role
            await t_cat.set_permissions(everyone, view_channel=True, send_messages=False)
            # Make all child channels inherit
            for ch in t_cat.channels:
                await ch.set_permissions(everyone, view_channel=True, send_messages=False)
            logger.info("Opened tournament category %s to @everyone", t_cat_id)
        except Exception as exc:
            logger.error("Failed to open tournament channels: %s", exc, exc_info=True)

    async def _lock_tournament_channels(self, guild: discord.Guild, reason_status: str) -> None:
        """Lock channels when tournament ends or is cancelled."""
        try:
            t_cat_id = self.t_settings.get("tournament_category_id")
            if not t_cat_id:
                return
            t_cat = guild.get_channel(int(t_cat_id))
            if not isinstance(t_cat, discord.CategoryChannel):
                return

            # Rename category to signal end state
            prefix = "🏆" if reason_status == "completed" else "❌"
            new_name = f"{prefix} {t_cat.name.lstrip('🏆 ').lstrip('❌ ')}"[:100]
            everyone = guild.default_role
            await t_cat.edit(name=new_name)

            # Post final message in announcements if it exists
            for ch in t_cat.channels:
                if "announcement" in ch.name and isinstance(ch, discord.TextChannel):
                    msg = (
                        "🏆 **Tournament Complete!** Thanks to all participants."
                        if reason_status == "completed"
                        else "❌ **Tournament Cancelled.** Apologies to all participants."
                    )
                    await ch.send(msg)
                    break

            logger.info("Locked tournament category %s (%s)", t_cat_id, reason_status)
        except Exception as exc:
            logger.error("Failed to lock tournament channels: %s", exc, exc_info=True)

    async def _update_manage_channel(self, interaction: discord.Interaction, new_status_value: str) -> None:
        """Post a status-change log in the management channel."""
        try:
            manage_ch_id = self.t_settings.get("manage_channel_id")
            if not manage_ch_id or not interaction.guild:
                return
            ch = interaction.guild.get_channel(int(manage_ch_id))
            if not isinstance(ch, discord.TextChannel):
                return

            embed = _make_manage_message(self.t_name, self.tournament_id, new_status_value, self.t_fmt)
            await ch.send(
                content=f"📊 Status changed to **{new_status_value.replace('_', ' ').title()}** by {interaction.user.mention}",
                embed=embed,
            )
        except Exception as exc:
            logger.warning("Could not update manage channel: %s", exc)
