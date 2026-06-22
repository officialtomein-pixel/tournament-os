"""
Notification event subscribers — listen for domain events and dispatch
real Discord notifications via discord_delivery.

Registered at startup by importing this module (decorators fire at import time).
Call register_all() explicitly if you need a no-op hook (e.g. for testing).
"""
import logging

from app.events.bus import event_bus

logger = logging.getLogger(__name__)


# ── Internal DB helpers ───────────────────────────────────────────────────────

async def _get_tournament_info(tournament_id: str, organization_id: str) -> dict:
    """Fetch tournament name + guild channel config for notification delivery."""
    try:
        from app.database.session import AsyncSessionLocal
        from app.database.models.tournament import Tournament
        from app.database.models.guild import Guild
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            t = await session.get(Tournament, tournament_id)
            if not t:
                return {}

            guild_q = select(Guild).where(
                Guild.organization_id == organization_id,
                Guild.deleted_at.is_(None),
            ).limit(1)
            guild = (await session.execute(guild_q)).scalar_one_or_none()
            guild_settings: dict = (guild.settings or {}) if guild else {}
            channel_ids: dict = guild_settings.get("channel_ids", {})
            tc: dict = t.channel_config or {}

            return {
                "name": t.name,
                "format": t.format.value if t.format else None,
                "announcements_channel_id": (
                    channel_ids.get("announcements")
                    or tc.get("announcements_channel_id")
                ),
                "schedule_channel_id": (
                    channel_ids.get("schedule")
                    or tc.get("schedule_channel_id")
                ),
                "staff_alerts_channel_id": (
                    channel_ids.get("staff_alerts")
                    or channel_ids.get("admin")
                    or tc.get("control_channel_id")
                ),
            }
    except Exception as exc:
        logger.warning("_get_tournament_info failed: %s", exc)
        return {}


async def _get_user_discord_id(registration_id: str) -> tuple[str | None, str | None]:
    """Return (discord_user_id, team_name) for a registration.

    Registration.submitted_by → User.discord_user_id
    """
    try:
        from app.database.session import AsyncSessionLocal
        from app.database.models.registration import Registration
        from app.database.models.team import Team
        from app.database.models.user import User

        async with AsyncSessionLocal() as session:
            reg = await session.get(Registration, registration_id)
            if not reg:
                return None, None

            # Look up the Discord ID via the User FK
            user = await session.get(User, reg.submitted_by)
            discord_id = user.discord_user_id if user else None

            team_name: str | None = None
            if reg.team_id:
                team = await session.get(Team, reg.team_id)
                if team:
                    team_name = team.name

            return discord_id, team_name
    except Exception as exc:
        logger.warning("_get_user_discord_id failed: %s", exc)
        return None, None


# ── Event subscribers ─────────────────────────────────────────────────────────

@event_bus.subscribe("RegistrationSubmitted")
async def on_registration_submitted(payload: dict) -> None:
    logger.info(
        "Registration submitted: reg_id=%s tournament_id=%s duplicates=%s",
        payload.get("registration_id"),
        payload.get("tournament_id"),
        payload.get("has_duplicates"),
    )
    # Staff alert: new pending registration
    t_info = await _get_tournament_info(
        payload.get("tournament_id", ""),
        payload.get("organization_id", ""),
    )
    if t_info.get("staff_alerts_channel_id") and payload.get("has_duplicates"):
        from app.services.notification.discord_delivery import _post_to_channel
        import discord
        e = discord.Embed(
            title="⚠️ Duplicate Registration Detected",
            description=f"A registration with possible duplicates was submitted for **{t_info.get('name', 'tournament')}**.",
            color=discord.Color.yellow(),
        )
        e.add_field(name="Reg ID", value=payload.get("registration_id", "")[:8], inline=True)
        e.set_footer(text="Review in the Registration panel.")
        await _post_to_channel(t_info["staff_alerts_channel_id"], e)


@event_bus.subscribe("RegistrationApproved")
async def on_registration_approved(payload: dict) -> None:
    logger.info(
        "Registration approved: reg_id=%s by=%s",
        payload.get("registration_id"),
        payload.get("reviewed_by"),
    )
    reg_id = payload.get("registration_id", "")
    t_info = await _get_tournament_info(
        payload.get("tournament_id", ""),
        payload.get("organization_id", ""),
    )
    discord_id, team_name = await _get_user_discord_id(reg_id)
    if discord_id and t_info.get("name"):
        from app.services.notification.discord_delivery import notify_registration_approved
        await notify_registration_approved(
            discord_id=discord_id,
            tournament_name=t_info["name"],
            team_name=team_name,
        )


@event_bus.subscribe("RegistrationRejected")
async def on_registration_rejected(payload: dict) -> None:
    logger.info(
        "Registration rejected: reg_id=%s reason=%s",
        payload.get("registration_id"),
        payload.get("reason"),
    )
    reg_id = payload.get("registration_id", "")
    t_info = await _get_tournament_info(
        payload.get("tournament_id", ""),
        payload.get("organization_id", ""),
    )
    discord_id, _ = await _get_user_discord_id(reg_id)
    if discord_id and t_info.get("name"):
        from app.services.notification.discord_delivery import notify_registration_rejected
        await notify_registration_rejected(
            discord_id=discord_id,
            tournament_name=t_info["name"],
            reason=payload.get("reason"),
        )


@event_bus.subscribe("MatchStarted")
async def on_match_started(payload: dict) -> None:
    logger.info("Match started: match_id=%s", payload.get("match_id"))


@event_bus.subscribe("MatchCompleted")
async def on_match_completed(payload: dict) -> None:
    logger.info(
        "Match completed: match_id=%s winner=%s",
        payload.get("match_id"),
        payload.get("winner_id"),
    )


@event_bus.subscribe("DisputeOpened")
async def on_dispute_opened(payload: dict) -> None:
    logger.info(
        "Dispute opened: dispute_id=%s type=%s",
        payload.get("dispute_id"),
        payload.get("case_type"),
    )
    t_info = await _get_tournament_info(
        payload.get("tournament_id", ""),
        payload.get("organization_id", ""),
    )
    if t_info.get("staff_alerts_channel_id"):
        from app.services.notification.discord_delivery import notify_dispute_opened
        await notify_dispute_opened(
            dispute_id_short=payload.get("dispute_id", "")[:8],
            tournament_name=t_info.get("name", "Tournament"),
            case_type=payload.get("case_type", "unknown"),
            description=payload.get("description", "A new dispute has been opened."),
            staff_alerts_channel_id=t_info["staff_alerts_channel_id"],
            opener_discord_id=payload.get("opener_discord_id"),
        )


@event_bus.subscribe("TournamentStatusChanged")
async def on_tournament_status_changed(payload: dict) -> None:
    old = payload.get("old_status", "")
    new = payload.get("new_status", "")
    tournament_id = payload.get("tournament_id", "")
    organization_id = payload.get("organization_id", "")
    logger.info(
        "Tournament status changed: tournament_id=%s %s -> %s",
        tournament_id,
        old,
        new,
    )
    t_info = await _get_tournament_info(tournament_id, organization_id)
    if t_info.get("announcements_channel_id") and t_info.get("name"):
        from app.services.notification.discord_delivery import notify_tournament_status
        await notify_tournament_status(
            tournament_name=t_info["name"],
            new_status=new,
            announcements_channel_id=t_info["announcements_channel_id"],
            extra_info=payload.get("extra_info"),
        )

    # Auto-snapshot at key lifecycle transitions
    if new in ("completed", "live") and tournament_id and organization_id:
        try:
            from app.database.session import AsyncSessionLocal
            from app.services.snapshot.snapshot_service import SnapshotService
            trigger = "tournament_completed" if new == "completed" else "tournament_live"
            label = f"Auto: {old} → {new}"
            async with AsyncSessionLocal() as s:
                async with s.begin():
                    svc = SnapshotService(s)
                    await svc.take(organization_id, tournament_id, trigger=trigger, label=label)
        except Exception as exc:
            logger.warning("Auto-snapshot failed for tournament %s: %s", tournament_id[:8], exc)


def register_all() -> None:
    """Called at startup to ensure all subscribers are imported and registered."""
    pass
