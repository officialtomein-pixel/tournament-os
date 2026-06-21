"""
Tournament lifecycle service — handles status transitions, validation,
and the Postgres LISTEN/NOTIFY cross-process sync.
"""
import asyncio
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.tournament import Tournament, TournamentStatus, VALID_TRANSITIONS
from app.database.repositories.audit import AuditRepository
from app.database.repositories.tournament import TournamentRepository
from app.events.publishers import tournament as t_pub

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = "tournament_os_events"


class TournamentLifecycleService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = TournamentRepository(session)
        self.audit = AuditRepository(session)

    async def transition_status(
        self,
        tournament_id: str,
        organization_id: str,
        new_status: TournamentStatus,
        actor_id: str,
        actor_type: str = "user",
    ) -> Tournament:
        tournament = await self.repo.get_by_id(tournament_id, organization_id)
        if not tournament:
            raise ValueError(f"Tournament {tournament_id} not found")

        # Normalize to full UUID in case a short prefix was passed in
        tournament_id = tournament.id

        if not tournament.can_transition_to(new_status):
            raise ValueError(
                f"Cannot transition from {tournament.status} to {new_status}. "
                f"Valid transitions: {[s.value for s in VALID_TRANSITIONS[tournament.status]]}"
            )

        old_status = tournament.status
        updated = await self.repo.update_status(tournament_id, organization_id, new_status)

        await self.audit.log(
            organization_id=organization_id,
            tournament_id=tournament_id,
            action="tournament.status_changed",
            actor_id=actor_id,
            actor_type=actor_type,
            target_type="tournament",
            target_id=tournament_id,
            payload={"old_status": old_status.value, "new_status": new_status.value},
        )

        await t_pub.tournament_status_changed(
            tournament_id, organization_id, old_status.value, new_status.value
        )

        # Notify other processes via Postgres NOTIFY
        await self._pg_notify(
            "tournament.status_changed",
            {
                "tournament_id": tournament_id,
                "organization_id": organization_id,
                "old_status": old_status.value,
                "new_status": new_status.value,
            },
        )

        return updated

    async def _pg_notify(self, event_type: str, payload: dict[str, Any]) -> None:
        """Send a cross-process event via PostgreSQL LISTEN/NOTIFY."""
        try:
            data = json.dumps({"type": event_type, **payload})
            # Escape single quotes for PG
            data_escaped = data.replace("'", "''")
            await self.session.execute(
                __import__("sqlalchemy").text(f"SELECT pg_notify('{NOTIFY_CHANNEL}', '{data_escaped}')")
            )
        except Exception as e:
            logger.warning("pg_notify failed (non-critical): %s", e)
