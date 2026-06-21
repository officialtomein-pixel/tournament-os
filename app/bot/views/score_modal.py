"""
Score submission modal.
"""
import discord
import logging

logger = logging.getLogger(__name__)


class ScoreModal(discord.ui.Modal, title="Submit Match Score"):
    score_team1 = discord.ui.TextInput(
        label="Your Team Score",
        placeholder="e.g. 2",
        required=True,
        max_length=10,
    )
    score_team2 = discord.ui.TextInput(
        label="Opponent Score",
        placeholder="e.g. 1",
        required=True,
        max_length=10,
    )
    notes = discord.ui.TextInput(
        label="Notes (optional)",
        placeholder="Any relevant details about the match",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, match_id: str, tournament_id: str, organization_id: str, team1_id: str, team2_id: str):
        super().__init__()
        self.match_id = match_id
        self.tournament_id = tournament_id
        self.organization_id = organization_id
        self.team1_id = team1_id
        self.team2_id = team2_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        from app.database.session import AsyncSessionLocal
        from app.services.match.score_handler import ScoreHandler
        from app.database.repositories.user import UserRepository
        from app.bot.helpers.formatters import success_embed, error_embed

        async with AsyncSessionLocal() as session:
            async with session.begin():
                try:
                    user_repo = UserRepository(session)
                    user, _ = await user_repo.get_or_create(str(interaction.user.id), interaction.user.name)

                    try:
                        s1 = int(self.score_team1.value.strip())
                        s2 = int(self.score_team2.value.strip())
                    except ValueError:
                        await interaction.followup.send(embed=error_embed("Scores must be integers."), ephemeral=True)
                        return

                    winner_id = self.team1_id if s1 > s2 else (self.team2_id if s2 > s1 else None)
                    loser_id = self.team2_id if s1 > s2 else (self.team1_id if s2 > s1 else None)

                    handler = ScoreHandler(session)
                    await handler.submit_score(
                        match_id=self.match_id,
                        tournament_id=self.tournament_id,
                        organization_id=self.organization_id,
                        submitted_by=user.id,
                        score_team1={"score": s1},
                        score_team2={"score": s2},
                        winner_id=winner_id,
                        loser_id=loser_id,
                    )

                    await interaction.followup.send(
                        embed=success_embed(f"Score submitted: **{s1} - {s2}**"),
                        ephemeral=True,
                    )
                except Exception as e:
                    logger.error("Score modal error: %s", e, exc_info=True)
                    await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


class ScoreOverrideModal(discord.ui.Modal, title="Override Match Score"):
    score_team1 = discord.ui.TextInput(label="Team 1 Score", required=True, max_length=10)
    score_team2 = discord.ui.TextInput(label="Team 2 Score", required=True, max_length=10)
    reason = discord.ui.TextInput(
        label="Override Reason",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, match_id: str, tournament_id: str, organization_id: str, team1_id: str, team2_id: str):
        super().__init__()
        self.match_id = match_id
        self.tournament_id = tournament_id
        self.organization_id = organization_id
        self.team1_id = team1_id
        self.team2_id = team2_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        from app.database.session import AsyncSessionLocal
        from app.services.match.score_handler import ScoreHandler
        from app.database.repositories.user import UserRepository
        from app.bot.helpers.formatters import success_embed, error_embed

        async with AsyncSessionLocal() as session:
            async with session.begin():
                try:
                    user_repo = UserRepository(session)
                    user, _ = await user_repo.get_or_create(str(interaction.user.id), interaction.user.name)
                    try:
                        s1 = int(self.score_team1.value.strip())
                        s2 = int(self.score_team2.value.strip())
                    except ValueError:
                        await interaction.followup.send(
                            embed=error_embed("Scores must be whole numbers (e.g. 2 and 1)."),
                            ephemeral=True,
                        )
                        return
                    winner_id = self.team1_id if s1 > s2 else (self.team2_id if s2 > s1 else None)
                    loser_id = self.team2_id if s1 > s2 else (self.team1_id if s2 > s1 else None)
                    handler = ScoreHandler(session)
                    await handler.submit_score(
                        match_id=self.match_id,
                        tournament_id=self.tournament_id,
                        organization_id=self.organization_id,
                        submitted_by=user.id,
                        score_team1={"score": s1},
                        score_team2={"score": s2},
                        winner_id=winner_id,
                        loser_id=loser_id,
                        is_override=True,
                        override_reason=self.reason.value,
                    )
                    await interaction.followup.send(
                        embed=success_embed(f"Score overridden to **{s1} - {s2}**. Reason: {self.reason.value}"),
                        ephemeral=True,
                    )
                except Exception as e:
                    await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
