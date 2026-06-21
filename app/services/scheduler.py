"""
Tournament auto-status scheduler.

Runs every 60 seconds and automatically transitions tournament statuses
based on the dates configured in the tournament wizard:

  registration_open_at  → REGISTRATION_OPEN
  registration_close_at → REGISTRATION_CLOSED
  checkin_open_at       → CHECKIN_OPEN
  checkin_close_at      → CHECKIN_CLOSED
  match_start_at        → LIVE (from CHECKIN_CLOSED)
  match_end_at          → COMPLETED (from LIVE / UNDER_REVIEW)
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def _run_once() -> None:
    from app.database.session import AsyncSessionLocal
    from app.database.models.tournament import Tournament, TournamentStatus
    from sqlalchemy import select

    now = datetime.now(tz=timezone.utc)

    watchable_values = [
        TournamentStatus.DRAFT.value,
        TournamentStatus.SCHEDULED.value,
        TournamentStatus.REGISTRATION_OPEN.value,
        TournamentStatus.REGISTRATION_CLOSED.value,
        TournamentStatus.CHECKIN_OPEN.value,
        TournamentStatus.CHECKIN_CLOSED.value,
        TournamentStatus.LIVE.value,
        TournamentStatus.UNDER_REVIEW.value,
    ]

    async with AsyncSessionLocal() as session:
        q = select(Tournament).where(
            Tournament.status.in_(watchable_values),
            Tournament.deleted_at.is_(None),
        )
        tournaments = (await session.execute(q)).scalars().all()

    for t in tournaments:
        try:
            new_status: TournamentStatus | None = None
            st = t.status

            if st in (TournamentStatus.DRAFT, TournamentStatus.SCHEDULED):
                if t.registration_open_at and now >= t.registration_open_at:
                    new_status = TournamentStatus.REGISTRATION_OPEN

            elif st == TournamentStatus.REGISTRATION_OPEN:
                if t.registration_close_at and now >= t.registration_close_at:
                    new_status = TournamentStatus.REGISTRATION_CLOSED

            elif st == TournamentStatus.REGISTRATION_CLOSED:
                if t.checkin_open_at and now >= t.checkin_open_at:
                    new_status = TournamentStatus.CHECKIN_OPEN
                elif t.match_start_at and now >= t.match_start_at:
                    # Skip check-in if no checkin_open_at configured
                    new_status = TournamentStatus.CHECKIN_CLOSED

            elif st == TournamentStatus.CHECKIN_OPEN:
                if t.checkin_close_at and now >= t.checkin_close_at:
                    new_status = TournamentStatus.CHECKIN_CLOSED

            elif st == TournamentStatus.CHECKIN_CLOSED:
                if t.match_start_at and now >= t.match_start_at:
                    new_status = TournamentStatus.LIVE

            elif st in (TournamentStatus.LIVE, TournamentStatus.UNDER_REVIEW):
                if t.match_end_at and now >= t.match_end_at:
                    new_status = TournamentStatus.COMPLETED

            if new_status and t.can_transition_to(new_status):
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        t_row = await session.get(Tournament, t.id)
                        if t_row and t_row.can_transition_to(new_status):
                            old_val = t_row.status.value
                            t_row.status = new_status
                            logger.info(
                                "Auto-status: %s → %s  tournament=%s (%s)",
                                old_val, new_status.value, t.id[:8], t.name,
                            )

        except Exception as exc:
            logger.error(
                "Scheduler error for tournament %s: %s", t.id[:8], exc, exc_info=True
            )


async def run_scheduler() -> None:
    """Loop: check all active tournaments every 60 seconds for date-based transitions."""
    logger.info("Tournament auto-status scheduler started (60-second interval)")
    while True:
        try:
            await _run_once()
        except Exception as exc:
            logger.error("Scheduler loop error: %s", exc, exc_info=True)
        await asyncio.sleep(60)
