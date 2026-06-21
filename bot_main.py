"""
Discord Bot entrypoint.
Connects to PostgreSQL DB and listens for cross-process events via NOTIFY.

IPv4 patch applied at module level — Railway containers do not support IPv6.
discord.py (aiohttp) and asyncpg both try IPv6 first; forcing IPv4 avoids
"OSError: [Errno 101] Network is unreachable" on Railway / most cloud runners.
"""
import asyncio
import logging
import socket
import sys

# ── IPv4-only patch ───────────────────────────────────────────────────────────
# Must be applied BEFORE any network library is imported so that aiohttp,
# asyncpg, and httpx all resolve hostnames to IPv4 addresses only.
_orig_getaddrinfo = socket.getaddrinfo

def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    results = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else results  # fall back to all results if no IPv4

socket.getaddrinfo = _ipv4_getaddrinfo
# ─────────────────────────────────────────────────────────────────────────────

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class TournamentBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        # Force IPv4 at the aiohttp level as well (belt-and-suspenders)
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        super().__init__(
            command_prefix="!",
            intents=intents,
            description="Tournament Operating System Bot",
            connector=connector,
        )
        self._pg_listener = None

    async def setup_hook(self) -> None:
        logger.info("Loading cogs...")

        cog_modules = [
            "app.bot.cogs.admin",
            "app.bot.cogs.registration",
            "app.bot.cogs.match_cog",
            "app.bot.cogs.ai_assistant_cog",
            "app.bot.cogs.dispute_cog",
        ]
        for cog in cog_modules:
            try:
                await self.load_extension(cog)
                logger.info("Loaded cog: %s", cog)
            except Exception as e:
                logger.error("Failed to load cog %s: %s", cog, e, exc_info=True)

        # Importing these modules registers all @event_bus.subscribe decorators
        import app.events.subscribers.notification_handler  # noqa: F401
        import app.events.subscribers.analytics_handler  # noqa: F401

        # Restore all persistent views so buttons survive bot restarts
        await self._restore_persistent_views()

        # Sync slash commands globally (background — may take up to 1h to propagate)
        try:
            synced = await self.tree.sync()
            logger.info("Synced %d slash commands globally", len(synced))
        except Exception as e:
            logger.error("Failed to sync commands globally: %s", e)

        # Wire up the app_commands tree error handler
        @self.tree.error
        async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
            logger.error(
                "App command error in '%s': %s",
                interaction.command.name if interaction.command else "?",
                error,
                exc_info=True,
            )
            msg = "❌ An unexpected error occurred. Please try again."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass

        # Start cross-process LISTEN/NOTIFY in the background
        asyncio.create_task(self._start_pg_listener())

        # Start the auto-status scheduler (date-based tournament transitions)
        from app.services.scheduler import run_scheduler
        asyncio.create_task(run_scheduler())

        # Start the autonomous tournament engine
        from app.services.autonomous_engine import run_autonomous_engine
        asyncio.create_task(run_autonomous_engine())

    async def _restore_persistent_views(self) -> None:
        """
        Re-register all persistent views (timeout=None) so Discord button
        interactions keep working after a bot restart.
        """
        try:
            from app.database.session import AsyncSessionLocal
            from app.database.models.tournament import Tournament, TournamentStatus
            from app.database.models.guild import Guild
            from app.database.models.registration import Registration, RegistrationStatus
            from app.bot.views.checkin_button import CheckInView
            from app.bot.views.tournament_create_view import TournamentCreateView
            from app.bot.views.tournament_manage_view import TournamentManageView
            from app.bot.views.registration_button_view import RegistrationButtonView
            from app.bot.views.player_hub_view import PlayerHubView
            from app.bot.views.support_ticket_view import SupportTicketView
            from app.bot.views.registration_card_view import RegistrationCardView
            from app.bot.views.control_panel_view import ControlPanelView
            from sqlalchemy import select

            self.add_view(PlayerHubView())
            logger.info("Restored PlayerHubView (static)")

            self.add_view(SupportTicketView())
            logger.info("Restored SupportTicketView (static)")

            async with AsyncSessionLocal() as session:
                checkin_q = (
                    select(Tournament)
                    .where(Tournament.status == TournamentStatus.CHECKIN_OPEN)
                    .where(Tournament.deleted_at.is_(None))
                )
                result = await session.execute(checkin_q)
                checkin_tournaments = result.scalars().all()
                for t in checkin_tournaments:
                    self.add_view(CheckInView(tournament_id=t.id, organization_id=t.organization_id))
                    logger.info("Restored CheckInView for tournament %s (%s)", t.id[:8], t.name)

                if not checkin_tournaments:
                    logger.info("No active check-in tournaments — no CheckInViews to restore")

                guilds_q = select(Guild).where(Guild.deleted_at.is_(None))
                guilds_result = await session.execute(guilds_q)
                guilds = guilds_result.scalars().all()

                create_count = 0
                for g in guilds:
                    settings_dict: dict = g.settings or {}
                    has_create_ch = (
                        settings_dict.get("create_tournament_channel_id") or
                        settings_dict.get("channel_ids", {}).get("create_tournament")
                    )
                    if has_create_ch:
                        self.add_view(TournamentCreateView(org_id=g.organization_id, guild_db_id=g.id))
                        create_count += 1

                logger.info("Restored %d TournamentCreateView(s)", create_count)

                all_t_q = select(Tournament).where(Tournament.deleted_at.is_(None))
                all_t_result = await session.execute(all_t_q)
                all_tournaments = all_t_result.scalars().all()

                manage_count = cp_count = reg_count = 0
                _ended = {TournamentStatus.COMPLETED, TournamentStatus.ARCHIVED, TournamentStatus.CANCELLED}

                for t in all_tournaments:
                    t_settings: dict = t.channel_config or {}
                    org_id = t.organization_id

                    if t_settings.get("manage_channel_id"):
                        self.add_view(TournamentManageView(tournament_id=t.id, org_id=org_id))
                        manage_count += 1
                        if t.status not in _ended:
                            self.add_view(ControlPanelView(tournament_id=t.id, org_id=org_id))
                            cp_count += 1

                    if (
                        t_settings.get("registration_channel_id")
                        and t.status == TournamentStatus.REGISTRATION_OPEN
                    ):
                        self.add_view(RegistrationButtonView(tournament_id=t.id, org_id=org_id))
                        reg_count += 1

                logger.info("Restored %d TournamentManageView(s)", manage_count)
                logger.info("Restored %d ControlPanelView(s)", cp_count)
                logger.info("Restored %d RegistrationButtonView(s)", reg_count)

                actionable_statuses = [
                    RegistrationStatus.PENDING,
                    RegistrationStatus.FLAGGED,
                    RegistrationStatus.WAITLISTED,
                    RegistrationStatus.CHANGES_REQUESTED,
                ]
                cards_q = (
                    select(Registration)
                    .where(Registration.status.in_(actionable_statuses))
                    .where(Registration.deleted_at.is_(None))
                )
                cards_result = await session.execute(cards_q)
                pending_regs = cards_result.scalars().all()

                card_count = 0
                for reg in pending_regs:
                    self.add_view(RegistrationCardView(registration_id=reg.id))
                    card_count += 1

                logger.info("Restored %d RegistrationCardView(s)", card_count)

        except Exception as e:
            logger.warning("Could not restore persistent views: %s", e, exc_info=True)

    async def _start_pg_listener(self) -> None:
        async def handle_event(event: dict) -> None:
            event_type = event.get("type", "")
            logger.info("PG cross-process event received: %s", event_type)
            from app.events.bus import event_bus
            await event_bus.publish(event_type, event)

        from app.services.notify_listener import PGNotifyListener
        listener = PGNotifyListener(settings.database_url, handle_event)
        self._pg_listener = listener
        try:
            await listener.start()
        except Exception as e:
            logger.error("PGNotifyListener failed: %s", e)

    async def on_ready(self) -> None:
        logger.info("Bot ready: %s (ID: %s)", self.user, self.user.id)
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="tournaments | /setup tournament",
        )
        await self.change_presence(activity=activity)

        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                guild_synced = await self.tree.sync(guild=guild)
                logger.info(
                    "Guild-synced %d commands to '%s' (%s)",
                    len(guild_synced), guild.name, guild.id,
                )
            except Exception as e:
                logger.warning("Could not guild-sync to '%s': %s", guild.name, e)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info("Joined guild: %s (ID: %s)", guild.name, guild.id)
        try:
            self.tree.copy_global_to(guild=guild)
            guild_synced = await self.tree.sync(guild=guild)
            logger.info(
                "Guild-synced %d commands to new guild '%s'",
                len(guild_synced), guild.name,
            )
        except Exception as e:
            logger.warning("Could not guild-sync to new guild '%s': %s", guild.name, e)

    async def close(self) -> None:
        logger.info("Shutting down bot...")
        if self._pg_listener:
            await self._pg_listener.stop()
        await super().close()


async def main() -> None:
    if not settings.discord_token:
        logger.critical("DISCORD_TOKEN is not set. Exiting.")
        sys.exit(1)

    if not settings.database_url:
        logger.critical("DATABASE_URL is not set. Exiting.")
        sys.exit(1)

    logger.info("Running database migrations...")
    proc = await asyncio.create_subprocess_exec(
        "alembic", "upgrade", "head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("Alembic migration output:\n%s", stderr.decode().strip())
    else:
        logger.info("Migrations OK:\n%s", stdout.decode().strip())

    # Retry loop — handles Discord rate limits and transient network errors
    delay = 10
    max_delay = 300
    attempt = 0

    while True:
        attempt += 1
        try:
            bot = TournamentBot()
            async with bot:
                await bot.start(settings.discord_token)
            break
        except discord.errors.HTTPException as exc:
            if exc.status == 429:
                retry_after = getattr(exc, "retry_after", None) or delay
                logger.warning(
                    "Discord rate-limited (attempt %d). Retrying in %.1fs…",
                    attempt, retry_after,
                )
                await asyncio.sleep(retry_after)
                delay = min(delay * 2, max_delay)
            else:
                logger.error("Discord HTTP error: %s", exc, exc_info=True)
                raise
        except (OSError, ConnectionError) as exc:
            logger.warning(
                "Network error (attempt %d): %s. Retrying in %ds…",
                attempt, exc, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


if __name__ == "__main__":
    asyncio.run(main())
