"""Tracks a persistent identity for users it sees in particular servers.

Whenever a user the cog doesn't recognize joins a server where the cog is
active, prompts the user, via DM, to indicate a team affiliation. In that
server and in every server where the cog is active, assigns that user to that
team.

This cog uses the [p]team command group.

Admin/Mod:
  [p]enable          Enables the cog in the current server.
  [p]disable         Disables the cog in the current server.
  [p]channel [chnl]  Sets admin messages to go to the designated channel.
                     All admin messages go to all admin channels.
!  [p]search [foo]    Searches for all teams whose names contain regex `foo`.
!  [p]ping [team] [msg]   DMs all members of `team` with `msg`.
  [p]reset           Resets all memory for all users, globally.

User:
  [p]whois <user>    States the team affiliation of the input user.
  [p]whoami          States the team affiliation of the sending user.
  [p]register [user]   Manually initiates the registration process for the
                       sending user.
  [p]forget [user]   Removes the sending user from their team affiliation.
  [p]ignore [user]   Opts out of receiving team_tracker pings.
  [p]unignore [user] Opts back in to receiving team_tracker pings.
"""
# xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

from redbot.core.bot import Red
from .team_tracker import TeamTracker


async def setup(bot: Red):
  tt_module = TeamTracker(bot)
  await tt_module.initialize()
  bot.add_cog(tt_module)
