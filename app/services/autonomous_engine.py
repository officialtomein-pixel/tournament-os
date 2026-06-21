"""
Autonomous Tournament Engine — 2.0 feature.

When autonomous_mode=True on a tournament, this engine handles the full lifecycle
without organizer involvement:

  1. Monitors active rounds — advances winners when all matches are decided
  2. Generates next round automatically
  3. Updates standings after each result
  4. Completes + archives tournament when all rounds are done

This runs as a background task alongside the scheduler.
Interval: 30 seconds (more aggressive than the 60s status scheduler).
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def _run_once() -> None:
    from app.database.session import AsyncSessionLocal
    from app.database.models.tournament import Tournament, TournamentStatus, TournamentFormat
    from app.database.models.match import Match, MatchStatus
    from app.database.models.bracket import Bracket
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        # Find all LIVE autonomous tournaments
        q = (
            select(Tournament)
            .where(
                Tournament.status == TournamentStatus.LIVE,
                Tournament.autonomous_mode.is_(True),
                Tournament.deleted_at.is_(None),
            )
        )
        tournaments = (await session.execute(q)).scalars().all()

    for t in tournaments:
        try:
            await _process_tournament(t.id, t.organization_id)
        except Exception as exc:
            logger.error(
                "Autonomous engine error for tournament %s: %s",
                t.id[:8], exc, exc_info=True,
            )


async def _process_tournament(tournament_id: str, organization_id: str) -> None:
    from app.database.session import AsyncSessionLocal
    from app.database.models.tournament import Tournament, TournamentStatus, TournamentFormat
    from app.database.models.match import Match, MatchStatus
    from app.database.models.bracket import Bracket
    from app.database.repositories.match import MatchRepository
    from app.database.repositories.tournament import TournamentRepository
    from app.services.bracket.advancement import BracketAdvancementService
    from sqlalchemy import select, and_

    async with AsyncSessionLocal() as session:
        t = await session.get(Tournament, tournament_id)
        if not t or t.status != TournamentStatus.LIVE:
            return

        # Get the active bracket
        bracket_q = select(Bracket).where(
            Bracket.tournament_id == tournament_id,
            Bracket.organization_id == organization_id,
        ).order_by(Bracket.stage.desc())
        bracket = (await session.execute(bracket_q)).scalars().first()
        if not bracket:
            return

        match_repo = MatchRepository(session)
        all_matches = await match_repo.list_all(organization_id, tournament_id)

        if not all_matches:
            return

        pending = [m for m in all_matches if m.status not in (MatchStatus.COMPLETED, MatchStatus.ARCHIVED)]
        completed = [m for m in all_matches if m.status == MatchStatus.COMPLETED]

        # Swiss: generate next round when current round is fully complete
        if t.format.value == "swiss":
            await _handle_swiss_auto(session, t, bracket, all_matches, pending)
            return

        # Elimination: advance winners and generate next round
        if t.format.value in ("single_elimination", "double_elimination", "triple_elimination"):
            if not pending:
                # All matches complete — tournament is done
                await _auto_complete_tournament(session, t, organization_id)
                return

        # No more work if there are still pending matches in the current round
        current_round = max((m.round for m in pending), default=0)
        current_round_pending = [m for m in pending if m.round == current_round]
        if current_round_pending:
            return  # Still waiting for current round to finish

        # All current round matches done — generate next round
        last_round = max((m.round for m in all_matches), default=0)
        if last_round == 0:
            return

        # Check if we should generate round last_round + 1
        last_round_matches = [m for m in all_matches if m.round == last_round]
        all_last_done = all(m.status == MatchStatus.COMPLETED for m in last_round_matches)
        if not all_last_done:
            return

        logger.info(
            "Autonomous: tournament %s round %s complete — generating round %s",
            tournament_id[:8], last_round, last_round + 1,
        )

        async with session.begin():
            from app.services.bracket.advancement import BracketAdvancementService
            adv = BracketAdvancementService(session)
            try:
                await adv.generate_next_round(organization_id, tournament_id, bracket.id)
            except ValueError as exc:
                # "No teams left" or "Tournament complete" — wrap up
                if "complete" in str(exc).lower() or "no teams" in str(exc).lower():
                    await _auto_complete_tournament(session, t, organization_id)
                else:
                    logger.warning("Next round generation failed for %s: %s", tournament_id[:8], exc)


async def _handle_swiss_auto(session, tournament, bracket, all_matches, pending) -> None:
    """Swiss: pair next round by current standings when all matches in current round finish."""
    from app.database.models.match import MatchStatus
    from app.services.bracket.advancement import BracketAdvancementService

    current_round = max((m.round for m in all_matches), default=0)
    round_matches = [m for m in all_matches if m.round == current_round]
    if any(m.status not in (MatchStatus.COMPLETED, MatchStatus.ARCHIVED) for m in round_matches):
        return  # Current round not done

    total_rounds = bracket.settings.get("total_rounds", 0)
    if total_rounds and current_round >= total_rounds:
        async with session.begin():
            t_row = await session.get(type(tournament), tournament.id)
            if t_row:
                from app.database.models.tournament import TournamentStatus
                t_row.status = TournamentStatus.COMPLETED
                logger.info("Autonomous Swiss: tournament %s completed after %s rounds", tournament.id[:8], current_round)
        return

    logger.info("Autonomous Swiss: generating round %s for tournament %s", current_round + 1, tournament.id[:8])
    async with session.begin():
        adv = BracketAdvancementService(session)
        try:
            await adv.generate_swiss_round(tournament.organization_id, tournament.id, bracket.id, current_round + 1)
        except Exception as exc:
            logger.warning("Swiss round generation failed: %s", exc)


async def _auto_complete_tournament(session, tournament, organization_id: str) -> None:
    """Transition tournament to COMPLETED status automatically."""
    from app.database.models.tournament import TournamentStatus
    from app.services.tournament.lifecycle import TournamentLifecycleService
    from app.database.repositories.tournament import TournamentRepository

    try:
        async with session.begin():
            repo = TournamentRepository(session)
            t = await repo.get_by_id(tournament.id, organization_id)
            if t and t.can_transition_to(TournamentStatus.COMPLETED):
                svc = TournamentLifecycleService(session)
                await svc.transition_status(
                    tournament_id=tournament.id,
                    organization_id=organization_id,
                    new_status=TournamentStatus.COMPLETED,
                    actor_id="autonomous_engine",
                    actor_type="system",
                )
                logger.info("Autonomous engine: tournament %s auto-completed", tournament.id[:8])
    except Exception as exc:
        logger.error("Auto-complete failed for tournament %s: %s", tournament.id[:8], exc)


async def run_autonomous_engine() -> None:
    """Loop: process all autonomous tournaments every 30 seconds."""
    logger.info("Autonomous tournament engine started (30-second interval)")
    while True:
        try:
            await _run_once()
        except Exception as exc:
            logger.error("Autonomous engine loop error: %s", exc, exc_info=True)
        await asyncio.sleep(30)
