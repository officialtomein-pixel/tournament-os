"""
Notification event subscribers — listen for domain events and dispatch notifications.
Registered at startup by calling register_all().
"""
import logging

from app.events.bus import event_bus

logger = logging.getLogger(__name__)


@event_bus.subscribe("RegistrationSubmitted")
async def on_registration_submitted(payload: dict) -> None:
    logger.info(
        "Registration submitted: reg_id=%s tournament_id=%s duplicates=%s",
        payload.get("registration_id"),
        payload.get("tournament_id"),
        payload.get("has_duplicates"),
    )


@event_bus.subscribe("RegistrationApproved")
async def on_registration_approved(payload: dict) -> None:
    logger.info(
        "Registration approved: reg_id=%s by=%s",
        payload.get("registration_id"),
        payload.get("reviewed_by"),
    )


@event_bus.subscribe("RegistrationRejected")
async def on_registration_rejected(payload: dict) -> None:
    logger.info(
        "Registration rejected: reg_id=%s reason=%s",
        payload.get("registration_id"),
        payload.get("reason"),
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


@event_bus.subscribe("TournamentStatusChanged")
async def on_tournament_status_changed(payload: dict) -> None:
    logger.info(
        "Tournament status changed: tournament_id=%s %s -> %s",
        payload.get("tournament_id"),
        payload.get("old_status"),
        payload.get("new_status"),
    )


def register_all() -> None:
    """Called at startup to ensure all subscribers are imported and registered."""
    pass
