"""
Bracket advancement service — promotes winners/losers to next match.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.match import Match, MatchStatus
from app.database.repositories.match import MatchRepository
from app.services.match.scheduler import MatchScheduler

logger = logging.getLogger(__name__)


class BracketAdvancement:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.match_repo = MatchRepository(session)
        self.scheduler = MatchScheduler(session)

    async def advance_winner(
        self,
        completed_match: Match,
        organization_id: str,
        tournament_id: str,
    ) -> None:
        """
        After a match completes, find the next match in the bracket
        and slot the winner in. For single elimination, bracket_data
        tracks match-to-next-match mapping.
        """
        if not completed_match.winner_id:
            return
        if not completed_match.bracket_id:
            return

        # Find next match for winner based on bracket structure
        next_match_number = (completed_match.match_number + 1) // 2
        next_round = completed_match.round + 1

        from sqlalchemy import select
        from app.database.models.match import Match as MatchModel
        q = (
            select(MatchModel)
            .where(MatchModel.organization_id == organization_id)
            .where(MatchModel.tournament_id == tournament_id)
            .where(MatchModel.bracket_id == completed_match.bracket_id)
            .where(MatchModel.round == next_round)
            .where(MatchModel.match_number == next_match_number)
        )
        result = await self.session.execute(q)
        next_match = result.scalar_one_or_none()

        if not next_match:
            logger.info("No next match found for winner — tournament may be complete")
            return

        # Slot winner into team1 or team2 position
        if completed_match.match_number % 2 == 1:
            next_match.team1_id = completed_match.winner_id
        else:
            next_match.team2_id = completed_match.winner_id

        await self.session.flush()
        logger.info(
            "Advanced winner %s to round %d match %d",
            completed_match.winner_id, next_round, next_match_number
        )

    async def generate_next_swiss_round(
        self,
        organization_id: str,
        tournament_id: str,
        bracket_id: str,
        current_round: int,
    ) -> list[Match]:
        """
        Swiss pairing for round N+1: pair teams with same win count,
        avoiding rematches where possible.
        """
        from sqlalchemy import select
        from app.database.models.standings import Standings
        from app.database.models.match import Match as MatchModel

        standings_q = (
            select(Standings)
            .where(Standings.organization_id == organization_id)
            .where(Standings.tournament_id == tournament_id)
            .order_by(Standings.wins.desc(), Standings.points.desc())
        )
        result = await self.session.execute(standings_q)
        standings = list(result.scalars().all())

        # Get all previous pairings to avoid rematches
        prev_q = (
            select(MatchModel)
            .where(MatchModel.bracket_id == bracket_id)
            .where(MatchModel.status == MatchStatus.COMPLETED)
        )
        prev_result = await self.session.execute(prev_q)
        prev_matches = list(prev_result.scalars().all())
        played_pairs: set[frozenset] = {
            frozenset([m.team1_id, m.team2_id]) for m in prev_matches if m.team1_id and m.team2_id
        }

        team_ids = [s.team_id for s in standings]
        pairings: list[tuple[str | None, str | None]] = []
        used: set[str] = set()

        for i, t1 in enumerate(team_ids):
            if t1 in used:
                continue
            for t2 in team_ids[i + 1:]:
                if t2 in used:
                    continue
                if frozenset([t1, t2]) not in played_pairs:
                    pairings.append((t1, t2))
                    used.add(t1)
                    used.add(t2)
                    break

        return await self.scheduler.schedule_round(
            organization_id, tournament_id, bracket_id,
            round=current_round + 1, pairings=pairings,
        )
