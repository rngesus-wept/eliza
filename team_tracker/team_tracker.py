"""Cog for tracking team affiliations globally."""

import asyncio

import discord
from redbot.core import commands
from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red


CONFIG_ID = 0x51C6FD2FD8E37013

DEFAULT_GLOBAL_SETTINGS = {
    'teams': {},
    'token': None,
}

DEFAULT_GUILD_SETTINGS = {
    'enabled': False,
    'admin_channel': None,
}

DEFAULT_USER_SETTINGS = {
    'display_name': '',  # Is this necessary?
    'team_id': -1,
}


class TeamTracker(commands.Cog):
  """Global team affiliation tracking."""

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, CONFIG_ID, force_registration=True)
    self.config.register_global(**DEFAULT_GLOBAL_SETTINGS)
    self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
    self.config.register_user(**DEFAULT_USER_SETTINGS)

    self.token = await self.config.token()

  async def initialize(self):
    self.bot.add_listener(self.member_join, 'on_member_join')

  async def member_join(self, member):
    # Reminder: A member is a user x guild combination; that is, the same user
    # in two different guilds will be represented by two different members!


  ######### General stuff

  @commands.group(name='team', invoke_with_command=True)
  async def _team(self, ctx: commands.Context):
    """Team affiliation tracking."""
    # Top-level group
    await ctx.send_help()

  ######### Admin stuff

  @_team.group(name='admin')
  @checks.mod_or_permissions(administrator=True)
  async def _admin(self, ctx: commands.Context):
    """Team affiliation administrator functions."""
    pass

  @_admin.command(name='channel')
  @commands.guild_only()
  @checks.mod_or_permissions(administrator=True)
  async def admin_channel(self, ctx: commands.Context):
    """Sets or displays the channel for admin message output.

    Set on a per-guild basis; multiple guilds may receive admin messages if
    they all have this setting on. Each guild gets only one such channel.

    Provide no arguments to see the value for the current guild. Provide
    a channel as an argument to point to that channel for the current guild.
    Provide any other string as an argument to unset the value for the current
    guild (though for sanity's sake it should probably be something
    False-looking.
    """
