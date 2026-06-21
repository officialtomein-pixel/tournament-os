"""
Bracket generator — supports all tournament formats.
Produces a bracket_data JSONB structure and schedules initial matches.
"""
import math
import logging
import random
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.bracket import Bracket
from app.database.models.team import Team
from app.database.models.tournament import TournamentFormat
from app.database.repositories.team import TeamRepository
from app.services.match.scheduler import MatchScheduler

logger = logging.getLogger(__name__)


def _seeded_pairings(teams: list[Team]) -> list[tuple[str | None, str | None]]:
    """Standard seed-based bracket pairing: 1v8, 2v7, etc."""
    n = len(teams)
    size = 1
    while size < n:
        size *= 2
    # Pad with byes
    padded = teams + [None] * (size - n)
    pairings = []
    for i in range(size // 2):
        t1 = padded[i]
        t2 = padded[size - 1 - i]
        pairings.append((t1.id if t1 else None, t2.id if t2 else None))
    return pairings


def _round_robin_schedule(teams: list[Team]) -> list[list[tuple[str, str | None]]]:
    """
    Generate round-robin schedule using the circle method.
    Returns list of rounds, each round is a list of (team1_id, team2_id) tuples.
    """
    ids = [t.id for t in teams]
    if len(ids) % 2:
        ids.append(None)  # bye
    n = len(ids)
    rounds = []
    for _ in range(n - 1):
        round_pairings = []
        for i in range(n // 2):
            t1 = ids[i]
            t2 = ids[n - 1 - i]
            if t1 and t2:
                round_pairings.append((t1, t2))
        rounds.append(round_pairings)
        ids = [ids[0]] + [ids[-1]] + ids[1:-1]
    return rounds


class BracketGenerator:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.scheduler = MatchScheduler(session)
        self.team_repo = TeamRepository(session)

    async def generate(
        self,
        organization_id: str,
        tournament_id: str,
        format: TournamentFormat,
        seeding_method: str = "seed",
        stage: int = 1,
    ) -> Bracket:
        teams = await self.team_repo.list_checked_in(organization_id, tournament_id)
        if not teams:
            raise ValueError("No checked-in teams to generate bracket for")

        if seeding_method == "random":
            random.shuffle(teams)
        else:
            teams.sort(key=lambda t: (t.seed or 9999))

        bracket = Bracket(
            organization_id=organization_id,
            tournament_id=tournament_id,
            bracket_type=format.value,
            stage=stage,
            name=f"Stage {stage} Bracket",
        )
        self.session.add(bracket)
        await self.session.flush()
        await self.session.refresh(bracket)

        if format in (
            TournamentFormat.SINGLE_ELIMINATION,
            TournamentFormat.DOUBLE_ELIMINATION,
            TournamentFormat.TRIPLE_ELIMINATION,
        ):
            await self._gen_elimination(bracket, teams, format)
        elif format == TournamentFormat.ROUND_ROBIN:
            await self._gen_round_robin(bracket, teams)
        elif format == TournamentFormat.SWISS:
            await self._gen_swiss_round1(bracket, teams)
        elif format == TournamentFormat.BATTLE_ROYALE:
            await self._gen_battle_royale(bracket, teams)
        elif format in (TournamentFormat.GROUP_STAGE, TournamentFormat.FREE_FOR_ALL):
            await self._gen_round_robin(bracket, teams)
        else:
            logger.warning("No specific generator for format %s — creating empty bracket", format)

        bracket.bracket_data = {
            "format": format.value,
            "team_count": len(teams),
            "seeding_method": seeding_method,
        }
        await self.session.flush()
        return bracket

    async def _gen_elimination(
        self, bracket: Bracket, teams: list[Team], format: TournamentFormat
    ) -> None:
        pairings = _seeded_pairings(teams)
        await self.scheduler.schedule_round(
            bracket.organization_id, bracket.tournament_id, bracket.id,
            round=1, pairings=pairings
        )

    async def _gen_round_robin(self, bracket: Bracket, teams: list[Team]) -> None:
        rounds = _round_robin_schedule(teams)
        for i, round_pairings in enumerate(rounds, start=1):
            await self.scheduler.schedule_round(
                bracket.organization_id, bracket.tournament_id, bracket.id,
                round=i, pairings=round_pairings
            )

    async def _gen_swiss_round1(self, bracket: Bracket, teams: list[Team]) -> None:
        """Swiss round 1 = random pairing; subsequent rounds use standings."""
        random.shuffle(teams)
        pairings = [(teams[i].id, teams[i + 1].id if i + 1 < len(teams) else None)
                    for i in range(0, len(teams), 2)]
        await self.scheduler.schedule_round(
            bracket.organization_id, bracket.tournament_id, bracket.id,
            round=1, pairings=pairings
        )

    async def _gen_battle_royale(self, bracket: Bracket, teams: list[Team]) -> None:
        """Create lobby assignments for Battle Royale — one match per lobby."""
        lobby_size = bracket.settings.get("lobby_size", 16)
        lobbies = math.ceil(len(teams) / lobby_size)
        for lobby in range(1, lobbies + 1):
            m = await self.scheduler.create_match(
                organization_id=bracket.organization_id,
                tournament_id=bracket.tournament_id,
                bracket_id=bracket.id,
                round=1,
                match_number=lobby,
                lobby_number=lobby,
            )
            # Assign teams to lobby via match metadata
            start = (lobby - 1) * lobby_size
            lobby_teams = teams[start:start + lobby_size]
            m.settings = {"lobby_teams": [t.id for t in lobby_teams]}
        await self.session.flush()
