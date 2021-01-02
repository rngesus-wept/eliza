"""Cog for tracking team affiliations globally."""

import asyncio
import logging
import requests
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
    'lookup_url': None,
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
  return 'http://localhost:8000/register_discord/%s/' % (
      user_id,)
  # return 'https://bts.hidden.institute/register_discord/%s/' % (
  #     user_id,)


def display(user):
  if getattr(user, 'guild', None):
    return f'{user.display_name} ({user.name}#{user.discriminator})'
  else:
    return f'{user.name}#{user.discriminator}'


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
    obj.channels = [bot.get_channel(channel_id)
                    for channel_id in data.get('channels', [])]
    obj.users = [bot.get_user(user_id)
                 for user_id in data.get('users', [])]
    obj.last_updated = data.get('last_updated', time.time())
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
    self.teams = {}
    async with self.config.teams() as teams:
      for key, value in teams.items():
        data = await TeamData.read(self.bot, value)
        self.teams[data.team_id] = data

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
              f'Adding registered user {display(member)}')

  async def registration_prompt(self, user, bypass_ignore=False):
    do_not_message = await self.config.user(user).do_not_message()
    if do_not_message and not bypass_ignore:
      return

    my_prefix = await self._prefix()
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

  async def update_user(self, user: discord.User = None, user_id: int = None):
    if user is None:
      user = self.bot.get_user(user_id)
    url = await self._update_url()
    params = {
        'auth': await self.config.secret(),
        'user_id': user_id or user.id,
    }

    response = requests.get(url, params=params)
    if response.status_code != 200:
      await self.admin_msg(
          'Attempt to refresh user data failed with error'
          f' {response.status_code}: {response.text}')
      return
    data = response.json()

    team_id = data['team'][0]
    if team_id not in self.teams:
      team_data = await TeamData.read(self.bot, {
          'team_id': data['team'][0],
          'display_name': data['team'][2],
          'username': data['team'][3]
      })
      await team_data.write(self.config)
      self.teams[team_id] = team_data
    else:
      team_data = self.teams[team_id]

    old_team = self.teams.get(await self.config.user(user).team_id(), None)
    if old_team:
      await self._remove_user_from_team(user, old_team)

    await self._add_user_to_team(user, team_data)

  async def update_team(self, team: TeamData = None, team_id: int = None):
    if team is None:
      team = self.teams[team_id]
    url = await self.config.lookup_url()
    params = {
        'auth': await self.config.secret(),
        'team_id': team_id or team['team_id'],
    }

    response = requests.get(url, params=params)
    if response.status_code != 200:
      await self.admin_msg(
          'Attempt to refresh team data failed with error'
          f' {response.status_code}: {response.text}')
      return
    data = response.json()

    original_users = set(user.id for user in team.users)
    updated_users = set(int(user_id) for user_id in data['user_ids'])
    ids_to_add = list(updated_users - original_users)
    users_to_add = [self.bot.get_user(user_id) for user_id in ids_to_add]
    ids_to_remove = list(original_users - updated_users)
    users_to_remove = [self.bot.get_user(user_id) for user_id in ids_to_remove]

    remove_results = await asyncio.gather(
        *[self._remove_user_from_team(user, team) for user in users_to_remove],
        return_exceptions=True)
    remove_errors = [user_id for user_id, result in zip(ids_to_remove, remove_results)
                     if result is not None]

    add_results = await asyncio.gather(
        *[self._add_user_to_team(user, team) for user in users_to_add],
        return_exceptions=True)
    add_errors = [user_id for user_id, result in zip(ids_to_add, add_results)
                  if result is not None]

    if add_errors or remove_errors:
      message = [f'Encountered unexpected errors while updating {team.username}:']
      if add_errors:
        message.append(f'    adding user ids {add_errors}')
      if remove_errors:
        message.append(f'    removing user ids {remove_errors}')
      await self.admin_msg('\n'.join(message))


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
        self.teams[team_id].display_name),)

  @_team.command(name='whois')
  async def team_whois(self, ctx: commands.Context, user: discord.User):
    """Show which team some user belongs to."""
    team_id = await self.config.user(user).team_id()

    if team_id == -1:
      await ctx.send(f'{display(user)} not affiliated with any team.')
      return
    if team_id not in self.teams:
      await ctx.send(f'{display(user)} is affiliated with a team'
                     f' whose ID I do not recognize. ({team_id})')
      return
    await ctx.send('%s is affiliated with Team **%s**.' % (
        display(user), self.teams[team_id].display_name))

  @_team.command(name='register')
  async def team_register(self, ctx: commands.Context, user: discord.User = None):
    """Manually initiate registration, possibly for another user.

    Any user may call this command on themself (with no argument). Only admins
    may call this command on others."""
    if user is not None:
      if not mod.is_mod_or_superior(ctx.author):
        await ctx.add_reaction(u'🙅')
        return
    else:
      user = ctx.author
    await self.registration_prompt(user, bypass_ignore=True)

  @_team.command(name='forget')
  async def team_forget(self, ctx: commands.Context, user: discord.User = None):
    """Remove team affiliation from target user (or self).

    Any user may call this command on themself (with no argument). Only admins
    may call this command on others."""
    if user is not None:
      if not mod.is_mod_or_superior(ctx.author):
        await ctx.add_reaction(u'🙅')
        return
    else:
      user = ctx.author
    team = self.teams.get(await self.config.user(user).team_id(), None)
    if team is not None:
      await self._remove_user_from_team(user, team)
    await ctx.send(f'Okay, I have forgotten all about {display(user)}.')

  @_team.command(name='ignore')
  async def team_ignore(self, ctx: command.Context):
    """Opt out of automated messages from the team management cog."""
    await self.config.user(ctx.author).do_not_message.set(True)
    await ctx.send('Okay, I won\'t DM about this anymore.')

  @_team.command(name='unignore')
  async def team_unignore(self, ctx: command.Context):
    """Opt (back) in to automated messages from the team management cog."""
    await self.config.user(ctx.author).do_not_message.set(False)
    await ctx.send('Okay, I\'ll include you back in team-wide DMs.')

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
  async def admin_local_ping(self, ctx: commands.Context,
                             team_identifier: str, *message):
    """Ping all members of the indicated team, locally.

    This directly replaces the `[p]team admin ping <team>` portion of the
    triggering message with mentions of all members of the named team in the
    current guild. It's kind of messy; maybe at-here is good enough?
    """
    pass

  @_admin.command(name='ping-all')
  @checks.mod_or_permissions(administrator=True)
  async def admin_global_ping(self, ctx: commands.Context,
                              team_identifier: str, *message):
    """Ping all members of the indicated team, globally.

    This issues pings to the indicated members *via DM*. Team members that have
    opted out using `[p]team ignore` will not receive the ping.
    """
    pass

  @_admin.command(name='secret')
  @checks.mod_or_permissions(administrator=True)
  async def admin_secret(self, ctx: commands.Context, *token: str):
    """Sets or displays the token used for talking to silenda."""
    the_token = await self.config.secret()
    token = ' '.join(token)
    if not token:
      await ctx.author.send(f'Team management secret: {the_token}')
    else:
      await self.config.secret.set(token)
      message = [display(ctx.author),
                 f'set the team management secret to {token}.']
      if the_token:
        message.append(f'(was `{the_token}`)')
      await self.admin_msg(' '.join(message))

  @_admin.command(name='lookup_url')
  @checks.mod_or_permissions(administrator=True)
  async def admin_lookup_url(self, ctx: commands.Context, *url: str):
    """Sets or displays the URL used for retrieving details from silenda."""
    the_url = await self.config.lookup_url()
    url = ' '.join(url)
    if not url:
      await ctx.author.send(f'Team management lookup url: {the_url}')
    else:
      await self.config.lookup_url.set(url)
      message = [display(ctx.author),
                 f'set the team management lookup url to {url}.']
      if the_url:
        message.append(f'(was `{the_url}`)')
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

  async def _prefix(self):
    return (await self.bot._prefix_cache.get_prefixes())[0]

  async def _update_url(self):
    url = await self.config.lookup_url()
    if not url:
      prefix = await self._prefix()
      await self.admin_msg(
          'Team lookup URL is not set. Use '
          f'`{prefix}team admin lookup_url <url>` to set it.')
      raise MissingCogSettingException('lookup_url is unset')
    return url

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

  async def _get_team_data(self, identifier):
    data = await self.config.get_raw('teams', identifier)
    return await TeamData.read(self.bot, data)

  async def _add_user_to_team(self, user: discord.User, team: TeamData):
    if user in team.users:
      log.debug(f'User {display(user)} is already on {team.username}.')
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
      log.debug(f'User {display(user)} is not on {team.username}.')
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

  ## DEBUG, delete before final deploy

  @commands.command(name='testjoin')
  async def simulate_join(self, ctx: commands.Context,
                          member: discord.Member = None):
    if member is not None:
      await self.member_join(member)
    else:
      await self.member_join(ctx.author)

  @commands.command(name='tt')
  async def my_debug(self, ctx: commands.Context):
    # await ctx.send(await self.config.teams())
    await ctx.send([user.display_name for user in self.teams[3].users])


class MissingCogSettingException(Exception):
  pass
