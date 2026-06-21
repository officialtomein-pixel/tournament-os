"""
Match score submission and override service.
All multi-step operations are wrapped in a transaction at the caller level.
After a winner is recorded, BracketAdvancement is called to slot the winner
into the next match (single/double/triple elimination formats).
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.match import Match, MatchStatus, BattleRoyaleResult
from app.database.repositories.audit import AuditRepository
from app.database.repositories.match import MatchRepository
from app.events.publishers import match as match_pub
from app.services.standings.calculator import StandingsCalculator

logger = logging.getLogger(__name__)


class ScoreHandler:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = MatchRepository(session)
        self.audit = AuditRepository(session)
        self.standings = StandingsCalculator(session)

    async def submit_score(
        self,
        match_id: str,
        tournament_id: str,
        organization_id: str,
        submitted_by: str,
        score_team1: dict,
        score_team2: dict,
        winner_id: str | None = None,
        loser_id: str | None = None,
        is_override: bool = False,
        override_reason: str | None = None,
    ) -> Match:
        match = await self.repo.get_by_id(match_id, organization_id, tournament_id)
        if not match:
            raise ValueError(f"Match {match_id} not found")
        match_id = match.id  # normalize short ID to full UUID
        tournament_id = match.tournament_id

        if match.status == MatchStatus.COMPLETED and not is_override:
            raise ValueError("Match already completed. Use score override to change the result.")

        updated = await self.repo.submit_score(
            match_id, organization_id, tournament_id,
            score_team1=score_team1,
            score_team2=score_team2,
            winner_id=winner_id,
            loser_id=loser_id,
            override_by=submitted_by if is_override else None,
            override_reason=override_reason,
        )

        action = "match.score_overridden" if is_override else "match.score_submitted"
        await self.audit.log(
            organization_id=organization_id,
            tournament_id=tournament_id,
            action=action,
            actor_id=submitted_by,
            target_type="match",
            target_id=match_id,
            payload={
                "score_team1": score_team1,
                "score_team2": score_team2,
                "winner_id": winner_id,
                "override_reason": override_reason,
            },
        )

        await match_pub.score_submitted(
            match_id, tournament_id, organization_id,
            submitted_by, score_team1, score_team2,
        )

        if winner_id:
            await match_pub.match_completed(
                match_id, tournament_id, organization_id, winner_id, loser_id
            )

            # Update standings for all formats
            await self.standings.update_after_match(
                organization_id, tournament_id, match_id,
                winner_id=winner_id, loser_id=loser_id,
            )

            # Advance winner to next match in elimination brackets
            await self._advance_bracket(updated, organization_id, tournament_id)

        return updated

    async def _advance_bracket(
        self,
        completed_match: Match,
        organization_id: str,
        tournament_id: str,
    ) -> None:
        """
        Advance the winner to the next bracket slot for elimination formats.
        Skipped gracefully for round-robin / swiss (no fixed next-match slot).
        """
        if not completed_match.bracket_id:
            return

        try:
            from app.database.repositories.tournament import TournamentRepository
            from app.database.models.tournament import TournamentFormat
            from app.services.bracket.advancement import BracketAdvancement

            t_repo = TournamentRepository(self.session)
            tournament = await t_repo.get_by_id(tournament_id, organization_id)
            if not tournament:
                return

            elimination_formats = {
                TournamentFormat.SINGLE_ELIMINATION,
                TournamentFormat.DOUBLE_ELIMINATION,
                TournamentFormat.TRIPLE_ELIMINATION,
            }
            if tournament.format not in elimination_formats:
                return

            advancement = BracketAdvancement(self.session)
            await advancement.advance_winner(
                completed_match=completed_match,
                organization_id=organization_id,
                tournament_id=tournament_id,
            )
        except Exception as e:
            # Non-fatal — log and continue; standings are already saved
            logger.error(
                "Bracket advancement failed for match %s: %s", completed_match.id, e, exc_info=True
            )
            # Write an audit entry so staff can discover the failure without log access
            try:
                await self.audit.log(
                    organization_id=organization_id,
                    tournament_id=tournament_id,
                    action="match.bracket_advancement_failed",
                    actor_id="system",
                    target_type="match",
                    target_id=completed_match.id,
                    payload={"error": str(e)},
                )
            except Exception:
                pass  # audit failure must never crash the score submission

    async def submit_battle_royale(
        self,
        match_id: str,
        tournament_id: str,
        organization_id: str,
        submitted_by: str,
        results: list[dict],
    ) -> list[BattleRoyaleResult]:
        """
        results: [{team_id, lobby_number, placement, kill_points, placement_points,
                   bonus_points, penalty_points, notes}]
        """
        match = await self.repo.get_by_id(match_id, organization_id, tournament_id)
        if not match:
            raise ValueError(f"Match {match_id} not found")
        match_id = match.id  # normalize short ID to full UUID
        tournament_id = match.tournament_id

        saved: list[BattleRoyaleResult] = []
        for r in results:
            br = BattleRoyaleResult(
                organization_id=organization_id,
                tournament_id=tournament_id,
                match_id=match_id,
                team_id=r["team_id"],
                lobby_number=r["lobby_number"],
                placement=r.get("placement"),
                kill_points=r.get("kill_points", 0),
                placement_points=r.get("placement_points", 0),
                bonus_points=r.get("bonus_points", 0),
                penalty_points=r.get("penalty_points", 0),
                notes=r.get("notes", {}),
            )
            saved.append(await self.repo.save_br_result(br))

        await self.repo.update_status(match_id, organization_id, tournament_id, MatchStatus.COMPLETED)
        await self.audit.log(
            organization_id=organization_id,
            tournament_id=tournament_id,
            action="match.br_results_submitted",
            actor_id=submitted_by,
            target_type="match",
            target_id=match_id,
            payload={"result_count": len(saved)},
        )
        await self.standings.update_br_standings(organization_id, tournament_id)
        return saved

    async def set_status(
        self,
        match_id: str,
        tournament_id: str,
        organization_id: str,
        status: MatchStatus,
        actor_id: str,
    ) -> Match:
        updated = await self.repo.update_status(match_id, organization_id, tournament_id, status)
        if not updated:
            raise ValueError(f"Match {match_id} not found")
        await self.audit.log(
            organization_id=organization_id,
            tournament_id=tournament_id,
            action=f"match.status_{status.value}",
            actor_id=actor_id,
            target_type="match",
            target_id=match_id,
            payload={"new_status": status.value},
        )
        if status == MatchStatus.LIVE:
            await match_pub.match_started(match_id, tournament_id, organization_id)
        return updated
