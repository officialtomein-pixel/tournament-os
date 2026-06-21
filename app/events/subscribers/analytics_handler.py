"""
Analytics event subscribers — listen for domain events and update aggregates.
"""
import logging

from app.events.bus import event_bus

logger = logging.getLogger(__name__)


@event_bus.subscribe("RegistrationSubmitted")
async def analytics_on_registration(payload: dict) -> None:
    logger.debug("Analytics: registration submitted for tournament %s", payload.get("tournament_id"))


@event_bus.subscribe("MatchCompleted")
async def analytics_on_match_completed(payload: dict) -> None:
    logger.debug("Analytics: match completed %s", payload.get("match_id"))


@event_bus.subscribe("DisputeOpened")
async def analytics_on_dispute(payload: dict) -> None:
    logger.debug("Analytics: dispute opened %s", payload.get("dispute_id"))


def register_all() -> None:
    pass
