"""Cog for tracking team affiliations globally."""

import asyncio
import logging
import time

import discord
from discord.ext import tasks
from redbot.core import commands
from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red
from redbot.core.utils import mod

from . import nl


log = logging.getLogger('red.eliza.team_tracker')

CONFIG_ID = 0x51C6FD2FD8E37013

DEFAULT_GLOBAL_SETTINGS = {
    'teams': {},
    'secret': None,
    ## TODO: Make registration URL a setting?
}

DEFAULT_GUILD_SETTINGS = {
    'enabled': False,
    'admin_channel': None,
}

DEFAULT_USER_SETTINGS = {
    'display_name': '',  # Is this necessary?
    'team_id': -1,
    'secret': None,  # salt for transmitting user ID
    'last_updated': 0,  # time.time()
    'do_not_message': False,
}

PERMIT_OVERWRITE = discord.PermissionOverwrite(view_channel=True)
FORBID_OVERWRITE = discord.PermissionOverwrite(view_channel=False)


def registration_url(user_id, token=None):
  return 'https://bts.hidden.institute/register_discord/%s/' % (
      user_id,)


def get_team_url(user_id, token=None):
  pass


def display_user_full(user):
  return f'{user.display_name} ({user.name}#{user.discriminator})'


class TeamData(object):
  """Structured data collected about teams."""
  team_id = -1
  display_name = None
  username = None
  channels = []  # list of objects, but serializes as a list of ids
  users = []  # list of objects, but serializes as a list of ids
  last_updated = 0  # time.time()

  @staticmethod
  async def read(bot: Red, data: dict):
    obj = TeamData()
    obj.team_id = data['team_id']
    obj.display_name = data['display_name']
    obj.username = data['username']
    obj.channels = [await bot.get_channel(channel_id)
                     for channel_id in data['channels']]
    obj.users = [await bot.get_user(user_id)
                  for user_id in data['users']]
    obj.last_updated = data['last_updated']
    return obj

  async def reload(self):
    # Re-retrieves all Channel and User objects
    self.channels = [await bot.get_channel(channel.id)
                     for channel in self.channels]
    self.users = [await bot.get_user(user.id)
                  for user in self.users]

  async def write(self, config: Config):
    serialized = {
        'team_id': self.team_id,
        'display_name': self.display_name,
        'username': self.username,
        'channels': [channel.id for channel in self.channels],
        'users': [user.id for user in self.users],
        'last_updated': self.last_updated,
    }
    await config.set_raw('teams', self.team_id, value=serialized)
    await config.set_raw('teams', self.username, value=serialized)
    # async with config.teams() as teams:
    #   teams[self.team_id] = serialized
    #   teams[self.username] = serialized


class TeamTracker(commands.Cog):
  """Global team affiliation tracking."""

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, CONFIG_ID, force_registration=True)
    self.config.register_global(**DEFAULT_GLOBAL_SETTINGS)
    self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
    self.config.register_user(**DEFAULT_USER_SETTINGS)

  async def initialize(self):
    # Load config information to internal memory
    self.token = await self.config.secret()
    self.teams = {}
    async with self.config.teams() as teams:
      for key, value in teams.items():
        if type(key) is not int:
          continue
        data = TeamData.read(self.bot, value)
        self.teams[data.team_id] = data
        self.teams[data.username] = data

    self.guilds = {}  # guild_id integer -> Guild object
    self.admin_channels = {}  # guild_id integer -> TextChannel object
    for guild_id in await self.config.all_guilds():
      await self._load_guild(guild_id=guild_id)

    self.bot.add_listener(self.member_join, 'on_member_join')

  async def member_join(self, member):
    # Reminder: A member is a user x guild combination; that is, the same user
    # in two different guilds will be represented by two different members!
    if member.bot:
      return
    team_id = await self.config.user(member).team_id()
    if team_id == -1:
      await self.registration_prompt(member)
    else:
      team_data = self.teams[team_id]
      for channel in team_data.channels:
        if channel.guild == member.guild:
          await self._permit_member_in_channel(
              member, channel,
              f'Adding registered user {display_user_full(member)}')

  async def registration_prompt(self, user, bypass_ignore=False):
    do_not_message = await self.config.user(user).do_not_message()
    if do_not_message and not bypass_ignore:
      return

    my_prefix = (await self.bot._prefix_cache.get_prefixes())[0]
    await user.send(
        'Hello! You\'re receiving this message because you joined the server'
        f' **{user.guild.name}** and are not associated with any particular'
        ' team.\n'
        f'    * To associate yourself with a team, visit <{registration_url(user.id)}>.\n'
        '        Please do not share this link other players, nor click on'
        ' links of this form sent to you by other players.\n'
        '    * To never receive team-related messages from me again, respond'
        f' with `{my_prefix}team ignore`. (Opt back in with `{my_prefix}team'
        ' unignore`.)')

  ######### General stuff

  @commands.group(name='team', invoke_without_command=True)
  async def _team(self, ctx: commands.Context):
    """Team affiliation tracking."""
    # Top-level group
    await ctx.send_help()

  @_team.command(name='whoami')
  async def team_whoami(self, ctx: commands.Context):
    """Show which team you belong to."""
    team_id = await self.config.user(ctx.author).team_id()

    if team_id == -1:
      await ctx.send('You are not affiliated with any team.')
      return
    if team_id not in self.teams:
      await ctx.send('You are affiliated with a team whose ID I do not'
                     f'recognize. ({team_id})')
      return
    await ctx.send('You are affiliated with Team **%s**.' % (
        self.teams[team_id]['display_name']),)

  @_team.command(name='whois')
  async def team_whois(self, ctx: commands.Context, user: discord.User):
    """Show which team some user belongs to."""
    team_id = await self.config.user(user).team_id()

    if team_id == -1:
      await ctx.send(f'{user.display_name} not affiliated with any team.')
      return
    if team_id not in self.teams:
      await ctx.send(f'{user.display_name} is affiliated with a team'
                     f' whose ID I do not recognize. ({team_id})')
      return
    await ctx.send('%s is affiliated with Team **%s**.' % (
        user.display_name, self.teams[team_id]['display_name']))

  @_team.command(name='register')
  async def team_register(self, ctx: commands.Context, user: discord.User = None):
    """Manually initiate registration, possibly for another user.

    Any user may call this command on themself (with no argument). Only admins
    may call this command on others."""
    ## TODO: Send in DM to user instead of in context
    if user is None:
      await ctx.send(registration_url(ctx.author.id))
    else:
      if not mod.is_mod_or_superior(ctx.author):
        await ctx.add_reaction(u'🙅')
      else:
        await ctx.send(registration_url(user.id))

  ######### Admin stuff

  @_team.group(name='admin', invoke_without_command=True)
  @checks.mod_or_permissions(administrator=True)
  async def _admin(self, ctx: commands.Context):
    """Team affiliation administrator functions."""
    await ctx.send_help()

  @_admin.command(name='reset')
  @checks.mod_or_permissions(administrator=True)
  async def admin_reset(self, ctx: commands.Context):
    """Resets all team management data, GLOBALLY."""
    await self.config.set_raw(value=DEFAULT_GLOBAL_SETTINGS)

    for guild_id in await self.config.all_guilds():
      await self._unload_guild(guild_id=guild_id)
      await self.config.guild_from_id(guild_id).set_raw(
          value=DEFAULT_GUILD_SETTINGS)

    for user_id in await self.config.all_users():
      await self.config.user_from_id(user_id).set_raw(
          value=DEFAULT_USER_SETTINGS)
    await ctx.send('Global team management factory reset complete.')

  @_admin.command(name='enable')
  @commands.guild_only()
  @checks.mod_or_permissions(administrator=True)
  async def admin_enable(self, ctx: commands.Context):
    """Enable team management in this server."""
    if ctx.guild.id in self.guilds:
      await ctx.send('Team management is already enabled in this guild.')
      return
    await self._enable_guild(guild=ctx.guild)
    await ctx.send('Team management enabled.')

  @_admin.command(name='disable')
  @commands.guild_only()
  @checks.mod_or_permissions(administrator=True)
  async def admin_disable(self, ctx: commands.Context):
    """Disable team management in this server."""
    if ctx.guild.id not in self.guilds:
      await ctx.send('Team management is already disabled in this guild.')
      return
    await self._disable_guild(guild=ctx.guild)
    await ctx.send('Team management disabled.')

  @_admin.command(name='channel')
  @commands.guild_only()
  @checks.mod_or_permissions(administrator=True)
  async def admin_channel(
      self, ctx: commands.Context, channel: discord.TextChannel=None):
    """Sets or displays the channel for admin message output.

    Set on a per-guild basis; multiple guilds may receive admin messages if
    they all have this setting on. Each guild gets only one such channel.

    Provide no arguments to see the value for the current guild. Provide
    a channel as an argument to point to that channel for the current guild.
    Provide any other string as an argument to unset the value for the current
    guild (though for sanity's sake it should probably be something
    False-looking).
    """
    the_channel = self.admin_channels.get(ctx.guild.id, None)
    if channel is None:
      if the_channel is None:
        await ctx.send(
            'No admin channel is set for team management in this guild.'
            ' To set one, provide this command with a reference to the'
            ' desired channel.')
      else:
        message = ['Admin messages for team management are routed to',
                   f' this guild\'s {the_channel.mention}']
        others = len(ch for ch in self.admin_channels.values() if ch) - 1
        if others:
          message.append(f' (and {others} other channel{nl.s(others)})')
        message.append('.')

        await ctx.send(''.join(message))
    else:
      await self._set_admin_channel(guild=ctx.guild, channel=channel)

      message = ['Team management admin messages are now routed to',
                 f' {channel.mention}.']
      await ctx.send(''.join(message))

  @_admin.command(name='ping')
  @commands.guild_only()
  @checks.mod_or_permissions(administrator=True)
  async def admin_local_ping(self, ctx: commands.Context, team_identifier):
    pass

  @_admin.command(name='secret')
  @checks.mod_or_permissions(administrator=True)
  async def admin_secret(self, ctx: commands.Context, token: str=None):
    """Sets or displays the token used for talking to silenda."""
    the_token = self.token
    if token is None:
      await ctx.author.send(f'Team management secret: {the_token}')
    else:
      await self._set_secret(token)
      message = [display_user_full(ctx.author),
                 'set the team management secret to',
                 f'`{self.token}`.']
      if the_token:
        message.append(f'(was `{the_token}`)')
      await self.admin_msg(' '.join(message))

  async def admin_msg(self, message):
    """Sends a message to all admin channels."""
    for channel in self.admin_channels.values():
      if channel:
        await channel.send(message)

  ######## Data management helper functions

  ## These are here to make sure that writes to the cog's internal memory are
  ## reflected to the datastore, and the reads from the cog's datastore are
  ## reflected to its internal memory.

  async def _load_guild(self,
                        guild: discord.Guild = None,
                        guild_id: int = None):
    if guild is None:
      guild = self.bot.get_guild(guild_id)
    if not await self.config.guild(guild).enabled():
      return

    self.guilds[guild.id] = guild
    await self._load_admin_channel(guild=guild)

  async def _enable_guild(self,
                          guild: discord.Guild = None,
                          guild_id: int = None):
    if guild is None:
      guild = self.bot.get_guild(guild_id)
    if guild.id in self.guilds:
      return

    await self.config.guild(guild).enabled.set(True)
    self.guilds[guild.id] = guild

    await self._load_admin_channel(guild=guild)

  async def _disable_guild(self,
                           guild: discord.Guild = None,
                           guild_id: int = None):
    if guild is None:
      guild = self.bot.get_guild(guild_id)
    if guild.id not in self.guilds:
      return

    await self.config.guild(guild).enabled.set(False)
    del self.guilds[guild.id]
    await self._unload_admin_channel(guild=guild)

  async def _load_admin_channel(self,
                                guild: discord.Guild = None,
                                guild_id: int = None):
    if guild is None:
      guild = self.bot.get_guild(guild_id)

    admin_channel_id = await self.config.guild(guild).admin_channel()
    if not admin_channel_id:
      self.admin_channels[guild.id] = None
    else:
      self.admin_channels[guild.id] = self.bot.get_channel(admin_channel_id)

  async def _unload_admin_channel(self,
                                  guild: discord.Guild = None,
                                  guild_id: int = None):
    if guild is None:
      guild = self.bot.get_guild(guild_id)
    if guild.id in self.admin_channels:
      del self.admin_channels[guild.id]

  async def _set_admin_channel(self,
                               guild: discord.Guild = None,
                               guild_id: int = None,
                               channel: discord.TextChannel = None,
                               channel_id: int = None):
    if guild is None:
      guild = self.bot.get_guild(guild_id)
    await self._enable_guild(guild=guild)

    if channel is None:
      channel = self.bot.get_channel(channel_id)

    await self.config.guild(guild).admin_channel.set(channel.id)
    self.admin_channels[guild.id] = channel

  async def _unset_admin_channel(self,
                                 guild: discord.Guild = None,
                                 guild_id: int = None):
    if guild is None:
      guild = self.bot.get_guild(guild_id)
    await self.config.guild(guild).admin_channel.set(0)
    self.admin_channels[guild.id] = None

  async def _set_secret(self, token):
    await self.config.secret.set(token)
    self.token = token

  async def _get_team_data(self, identifier):
    data = await self.config.get_raw('teams', identifier)
    return await TeamData.read(self.bot, data)

  async def _add_user_to_team(self, user: discord.User, team: TeamData):
    if user in team.users:
      log.debug(f'User {display_user_full(user)} is already on {team.username}.')
      return
    team.users.append(user)
    await team.write(self.config)

    await self.config.user(user).team_id.set(team.team_id)
    await self.config.user(user).last_updated.set(time.time())

    for channel in team.channels:
      await self._permit_user_in_channel(
          user, channel, f'Adding {user.name} to {team.username}')

  async def _remove_user_from_team(self, user: discord.User, team: TeamData):
    if user not in team.users:
      log.debug(f'User {display_user_full(user)} is not on {team.username}.')
      return
    team.users.remove(user)
    await team.write(self.config)

    await self.config.user(user).team_id.set(-1)
    await self.config.user(user).last_updated.set(time.time())

    for channel in team.channels:
      await self._forbid_user_in_channel(
          user, channel, f'Removing {user.name} from {team.username}')

  ## NOTE: Channels will be created such that users have all the necessary
  ## permissions EXCEPT being able to see the channel. This means that
  ## general user permissions on all channels is to be gated solely on
  ## visibility and no other permission.

  async def _permit_user_in_channel(self, user: discord.User,
                                    channel: discord.abc.GuildChannel,
                                    reason: str = None):
    guild = channel.guild
    member = await guild.get_member(user.id)
    if member is None:
      # User is not in the guild containing the channel
      return
    await self._permit_member_in_channel(member, channel, reason=reason)


  async def _permit_member_in_channel(self, member: discord.Member,
                                      channel: discord.abc.GuildChannel,
                                      reason: str = None):
    await channel.set_permissions(member, overwrite=PERMIT_OVERWRITE,
                                  reason=reason)


  async def _forbid_user_in_channel(self, user: discord.User,
                                    channel: discord.abc.GuildChannel,
                                    reason: str = None):
    guild = channel.guild
    member = await guild.get_member(user.id)
    if member is None:
      # User is not in the guild containing the channel
      return
    await self._forbit_member_in_channel(member, channel, reason=reason)

  async def _forbid_member_in_channel(self, member: discord.Member,
                                      channel: discord.abc.GuildChannel,
                                      reason: str = None):
    await channel.set_permissions(member, overwrite=FORBID_OVERWRITE,
                                  reason=reason)

  async def _permit_team_in_channel(self, team: TeamData,
                                    channel: discord.abc.GuildChannel):
    team.channels.append(channel)
    await team.write(self.config)

    for user in team.users:
      await self._permit_user_in_channel(
          user, channel, f'Adding {team.username}')

  async def _forbid_team_in_channel(self, team: TeamData,
                                    channel: discord.abc.GuildChannel):
    team.channels.remove(channel)
    await team.write(self.config)

    for user in team.users:
      await self._forbid_user_in_channel(
          user, channel, f'Removing {team.username}')

  ## DEBUG

  @commands.command(name='testjoin')
  async def simulate_join(self, ctx: commands.Context,
                          member: discord.Member = None):
    if member is not None:
      await self.member_join(member)
    else:
      await self.member_join(ctx.author)

  @commands.command(name='tt')
  async def my_debug(self, ctx: commands.Context):
    # team_data = TeamData()
    # team_data.team_id = 1
    # team_data.display_name = 'Test team'
    # team_data.username = 'test'
    # await team_data.write(self.config)
    # await self._add_user_to_team(ctx.author, team_data)

    await self.config.user(ctx.author).team_id.set(2)
    data = await self.config.user(ctx.author)()
    await ctx.send(repr(data))
