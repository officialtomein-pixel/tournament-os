"""
Discord message formatting helpers.
"""
import discord
from datetime import datetime, timezone


def _discord_ts(dt: datetime | None, style: str = "F") -> str:
    """Return a Discord timestamp string <t:UNIX:style> or 'Not set'."""
    if not dt:
        return "Not set"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:{style}>"


def tournament_embed(tournament) -> discord.Embed:
    color = {
        "registration_open": discord.Color.green(),
        "live": discord.Color.red(),
        "completed": discord.Color.greyple(),
        "cancelled": discord.Color.dark_red(),
    }.get(tournament.status.value, discord.Color.blurple())

    embed = discord.Embed(
        title=f"🏆 {tournament.name}",
        description=tournament.description or "No description provided.",
        color=color,
    )
    embed.add_field(name="Game",   value=tournament.game, inline=True)
    embed.add_field(name="Format", value=tournament.format.value.replace("_", " ").title(), inline=True)
    embed.add_field(name="Status", value=tournament.status.value.replace("_", " ").title(), inline=True)

    if tournament.prize_pool:
        embed.add_field(name="Prize Pool", value=tournament.prize_pool, inline=True)
    if tournament.max_teams:
        embed.add_field(name="Max Teams", value=str(tournament.max_teams), inline=True)
    if tournament.region:
        embed.add_field(name="Region", value=tournament.region, inline=True)
    if tournament.registration_open_at:
        embed.add_field(name="Registration Opens", value=_discord_ts(tournament.registration_open_at), inline=False)
    if tournament.registration_close_at:
        embed.add_field(name="Registration Closes", value=_discord_ts(tournament.registration_close_at), inline=True)
    if tournament.match_start_at:
        embed.add_field(name="Matches Start", value=_discord_ts(tournament.match_start_at), inline=True)

    embed.set_footer(text=f"ID: {tournament.id[:8]}")
    return embed


def registration_embed(registration, status_override: str | None = None) -> discord.Embed:
    status = status_override or registration.status.value
    colors = {
        "pending":            discord.Color.yellow(),
        "auto_approved":      discord.Color.green(),
        "manually_approved":  discord.Color.green(),
        "rejected":           discord.Color.red(),
        "flagged":            discord.Color.orange(),
        "changes_requested":  discord.Color.orange(),
        "hold":               discord.Color.greyple(),
    }
    embed = discord.Embed(
        title="Registration Status",
        color=colors.get(status, discord.Color.blurple()),
    )
    embed.add_field(name="Status",          value=status.replace("_", " ").title(), inline=True)
    embed.add_field(name="Registration ID", value=registration.id[:8],              inline=True)
    if registration.rejection_reason:
        embed.add_field(name="Reason", value=registration.rejection_reason, inline=False)
    submitted = _discord_ts(registration.created_at, style="R")
    embed.set_footer(text=f"Submitted {submitted}")
    return embed


def match_embed(match, team1_name: str = "Team 1", team2_name: str = "Team 2") -> discord.Embed:
    status_colors = {
        "live":           discord.Color.red(),
        "completed":      discord.Color.green(),
        "scheduled":      discord.Color.blue(),
        "awaiting_score": discord.Color.yellow(),
    }
    embed = discord.Embed(
        title=f"Match — Round {match.round or '?'} #{match.match_number or '?'}",
        color=status_colors.get(match.status.value, discord.Color.blurple()),
    )
    embed.add_field(name="Team 1", value=team1_name, inline=True)
    embed.add_field(name="vs",     value="⚔️",        inline=True)
    embed.add_field(name="Team 2", value=team2_name,  inline=True)
    embed.add_field(name="Status", value=match.status.value.replace("_", " ").title(), inline=True)
    if match.scheduled_at:
        embed.add_field(name="Scheduled", value=_discord_ts(match.scheduled_at), inline=True)
    embed.set_footer(text=f"Match ID: {match.id[:8]}")
    return embed


def standings_embed(standings_list: list, tournament_name: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"📊 Standings — {tournament_name}",
        color=discord.Color.gold(),
    )
    lines = []
    for s in standings_list[:20]:
        rank = s.get("rank")
        rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank or '?'}")
        lines.append(
            f"{rank_emoji} **{s.get('team_name', 'Unknown')}** — "
            f"{s.get('points', 0)} pts ({s.get('wins', 0)}W/{s.get('losses', 0)}L)"
        )
    embed.description = "\n".join(lines) if lines else "No standings yet."
    return embed


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="Error", description=message, color=discord.Color.red())


def success_embed(message: str, title: str = "Success") -> discord.Embed:
    return discord.Embed(title=title, description=message, color=discord.Color.green())
