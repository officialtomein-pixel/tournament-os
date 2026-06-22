"""
Admin Cog — tournament admin operations are now button-driven via the Control Panel.

Removed slash commands (replaced by Control Panel buttons):
  /tournament_generate_bracket → Control Panel 🏆 Bracket → ⚡ Generate Bracket
  /analytics                   → Control Panel 📊 Analytics

Remaining player-facing slash commands live in match_cog.py:
  /standings   — public standings board
  /my_matches  — player's upcoming matches
"""
import logging
from discord.ext import commands

logger = logging.getLogger(__name__)


class AdminCog(commands.Cog, name="admin"):
    """Admin bracket/analytics operations live in the Control Panel — this cog is a registry stub."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
