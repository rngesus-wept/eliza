"""Cog for tracking team affiliations globally."""

import asyncio
import copy
import fuzzywuzzy
import hashlib
import logging
import os
import pathlib
import random
import requests
import time
from typing import List, Optional

import discord
from discord.ext import tasks
from redbot.core import commands
from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red
from redbot.core.utils import mod
from redbot.core.utils.menus import menu, prev_page, next_page

from . import nl


log = logging.getLogger('red.eliza.team_tracker')

CONFIG_ID = 0x51C6FD2FD8E37013

DEFAULT_GLOBAL_SETTINGS = {
    'teams': {},
    'server_url': None,
    'lookup_url': None,
    'secret': None,
    ## TODO: Make registration URL a setting?
    'undigest': {},  # digest -> user_id
}

DEFAULT_GUILD_SETTINGS = {
    'enabled': False,
    'admin_channel': None,
    'teams_category': None,
    'participant_role': None,
}

DEFAULT_USER_SETTINGS = {
    'display_name': '',  # Is this necessary?
    'team_id': -1,
    'secret': None,  # salt for transmitting user ID
    'digest': None,  # This is a digest of user ID + secret somehow
    'last_updated': 0,  # time.time()
    'do_not_message': False,
    # We use this to stagger automated user refreshes (to avoid spikyness). I
    # would use the user ID but I do not believe those to be uniformly
    # distributed.
    'backoff_factor': 1,
    'refresh_modulus': -1,
}

PARTICIPANT_ROLE_NAME = 'participant'

CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'

MOD_PERM = discord.PermissionOverwrite.from_pair(
    discord.Permissions.all(), discord.Permissions.none())
PARTICIPANT_PERM = discord.PermissionOverwrite(
    send_messages=True, add_reactions=True, use_external_emojis=True,
    speak=True, stream=True, use_voice_activation=True)
TEAMMATE_PERM = discord.PermissionOverwrite(
    view_channel=True, read_message_history=True, connect=True)
DEFAULT_PERM= discord.PermissionOverwrite(
    view_channel=False, send_messages=False, add_reactions=False,
    read_message_history=False, connect=False, speak=False, stream=False)


def display(user) -> str:
  if getattr(user, 'guild', None):
    return f'{user.display_name} ({user.name}#{user.discriminator})'
  else:
    return f'{user.name}#{user.discriminator}'

def random_channel_name() -> str:
  return f'room-{random.randint(1,64)}-{str(random.randint(10,599)).zfill(3)}'

def random_salt() -> str:
  return ''.join(random.choices(CHARS, k=12))

def digest(user_id, salt) -> str:
  return hashlib.sha224(f'{salt[:4]}{user_id}{salt[4:]}'.encode('utf-8')).hexdigest()


## Menu utilities

async def close_menu(ctx: commands.Context, pages: list, controls: dict,
                     message: discord.Message, page: int, timeout: float, emoji: str):
  ## This overrides the normal "close" behavior in redbot.core.utils.menus in
  ## that it clears reactions instead of deleting the search results.
  try:
    await message.clear_reactions()
  except discord.Forbidden:
    for key in controls.keys():
      await message.remove_reaction(key, ctx.bot.user)
  return None

DEFAULT_CONTROLS = {"⬅": prev_page, "❌": close_menu, "➡": next_page}

def paginate_team_data(members: List[discord.Member],
                             users: List[discord.User],
                             channels: List[discord.abc.GuildChannel]) -> List[str]:
  members_txt = [display(member) for member in members]
  users_txt = [display(user) for user in users]
  channels_txt = [channel.mention for channel in channels]

  pages = []
  current_page = [f'**{len(members_txt) + len(users_txt)}** Registered Members', '']
  current_page_count = len(current_page[0]) + 2
  if channels_txt:
    current_page.append('**Channels**')
    current_page_count += len(current_page[-1])
    for line in channels_txt:
      if current_page_count > 2000:
        pages.append('\n'.join(current_page))
        current_page = ['**Channels (cont\'d)**'],
        current_page_count = len(current_page[0])
      current_page.append(line)
      current_page_count += len(line) + 1  # plus 1 for newline in eventual join
    if members_txt or users_txt:
      current_page.append('')  # becomes a newline
      current_page_count += 1
  if members_txt:
    if current_page_count > 1500:
      pages.append('\n'.join(current_page))
      current_page, current_page_count = [], 0
    current_page.append(f'**Members in Server** (**{len(members_txt)}** total)')
    current_page_count += len(current_page[-1])
    for line in members_txt:
      if current_page_count > 2000:
        pages.append('\n'.join(current_page))
        current_page = ['**Members in Server** (cont\'d)'],
        current_page_count = len(current_page[0])
      current_page.append(line)
      current_page_count += len(line) + 1  # plus 1 for newline in eventual join
    if users_txt:
      current_page.append('')  # becomes a newline
      current_page_count += 1
  if users_txt:
    if current_page_count > 1500:
      pages.append('\n'.join(current_page))
      current_page, current_page_count = [], 0
    current_page.append(f'**Members Elsewhere** (**{len(users_txt)}** total)')
    current_page_count += len(current_page[-1])
    for line in users_txt:
      if current_page_count > 2000:
        pages.append('\n'.join(current_page))
        current_page = ['**Members Elsewhere** (cont\'d)'],
        current_page_count = len(current_page[0])
      current_page.append(line)
      current_page_count += len(line) + 1  # plus 1 for newline in eventual join
  pages.append('\n'.join(current_page))

  return pages

def paginate_table(lines):
  pages = []
  current_page, current_count = [], 0
  current_page.append("```")
  current_count += len(current_page[0]) + 1
  for line in lines:
    current_page.append(line)
    current_count += len(line) + 1
    if current_count > 1500:
      current_page.append("```")
      pages.append('\n'.join(current_page))
      current_page = "```"
      current_count = len(current_page[0]) + 1
  current_page.append("```")
  pages.append('\n'.join(current_page))
  return pages


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
    self.channels = [bot.get_channel(channel.id)
                     for channel in self.channels]
    self.users = [bot.get_user(user.id)
                  for user in self.users]

  async def write(self, config: Config):
    self.channels = [ch for ch in self.channels if ch is not None]
    self.users = [us for us in self.users if us is not None]
    serialized = {
        'team_id': self.team_id,
        'display_name': self.display_name,
        'username': self.username,
        'channels': [channel.id for channel in self.channels],
        'users': [user.id for user in self.users if user is not None],
        'last_updated': self.last_updated,
    }
    await config.set_raw('teams', self.team_id, value=serialized)

  def users_here(self, guild: discord.Guild):
    members = [guild.get_member(user.id)
               for user in self.users if user is not None]
    return [member for member in members if member is not None]

  def table_line(self, guild: discord.Guild, count: int=1):
    data = [f'{self.team_id:3d}',
            f'{self.username:24}']
    users_here = self.users_here(guild)
    members = [f'@{user.name}#{user.discriminator}'
               for user in self.users_here(guild)[:count]]
    others = len(users_here) - len(members)
    if others:
      members.append(f'{others} more')
    if len(members) > 2:
      members[-1] = 'and ' + members[-1]
    if len(members) == 2:
      data.append(' and '.join(members))
    else:
      data.append(', '.join(members))
    if members:
      return '  '.join(data)
    else:
      return ''


class TeamTracker(commands.Cog):
  """Global team affiliation tracking."""

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, CONFIG_ID, force_registration=True)
    self.config.register_global(**DEFAULT_GLOBAL_SETTINGS)
    self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
    self.config.register_user(**DEFAULT_USER_SETTINGS)

  async def initialize(self):
    await self.initialize_internals()
    self.bot.add_listener(self.member_join, 'on_member_join')
    self.cron_update_teams.start()
    self.cron_update_users.start()

  async def initialize_internals(self):
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

  def cog_unload(self):
    self.cron_update_teams.cancel()
    self.cron_update_users.cancel()

  async def member_join(self, member):
    log.info('member_join triggered')
    # Reminder: A member is a user x guild combination; that is, the same user
    # in two different guilds will be represented by two different members!
    if member.bot:
      log.info('member was a bot')
      return
    enabled = await self.config.guild(member.guild).enabled()
    if not enabled:
      log.info('guild does not have team tracking enabled')
      return
    team_id = await self.config.user(member).team_id()
    if team_id == -1:
      log.info('sending reg message')
      if not await self.registration_prompt(member):
        await self.admin_msg(f'Discord registration for {member.name}#{member.discriminator}'
                             ' failed; user may have the bot blocked, or have DMs from non-'
                             'friends disabled.')
      await self.config.user(member).backoff_factor.set(1)
    else:
      team_data = self.teams[team_id]
      log.info(f'applying local config for team {team_data.display_name}')
      for channel in team_data.channels:
        if channel.guild == member.guild:
          await self._permit_member_in_channel(
              member, channel,
              f'Adding registered user {display(member)}')

  async def registration_prompt(self, user, bypass_ignore=False) -> bool:
    do_not_message = await self.config.user(user).do_not_message()
    if do_not_message and not bypass_ignore:
      return True  # message fails silently as intended

    my_prefix = await self._prefix()
    if getattr(user, 'guild', None):
      intro = (
          'Hello! You\'re receiving this message either because you or an event'
          ' admin requested it, or because you joined the server **%s** and I'
          ' don\'t know which team you\'re on.'
      ) % (user.guild.name,)
    else:
      intro = (
          'Hello! You\'re receiving this message because you or an event admin'
          ' requested it.'
      )
    register_instructions = (
        '    * To let me know which team you\'re on, visit <%s>.\n'
        '          Please do not share this link other players, nor click on'
        ' links of this form sent to you by other players.'
    ) % (os.path.join(await self._register_url(),
                      await self._token(user=user)))
    ignore_instructions = (
        '    * To never receive team-related messages from me again, respond'
        ' with `%steam ignore`. (Opt back in with `%steam unignore`.)'
    ) % (my_prefix, my_prefix)
    try:
      await user.send('\n'.join([
          intro, register_instructions, ignore_instructions]))
      return True
    except discord.http.Forbidden:
      return False

  @tasks.loop(minutes=1.0)  # Actual loop time for an individual team is 30 minutes
  async def cron_update_teams(self):
    wall_modulus = int(time.time() / 60) % 30
    teams_to_update = [team_id for team_id in self.teams
                       if team_id % 30 == wall_modulus]
    if teams_to_update:
      log.info(f'Running automated update for {len(teams_to_update)}'
               f' team{nl.s(len(teams_to_update))}.')
      await asyncio.gather(
          *[self._update_team(team=self.teams[team_id]) for team_id in teams_to_update],
          return_exceptions=True)

  @tasks.loop(seconds=10.0)
  async def cron_update_users(self):
    wall_modulus = int(time.time() / 10) % 6
    users = await self.config.all_users()
    users_to_update = [user_id for user_id in users
                       if (time.time() - users[user_id]['last_updated'] >=
                           await self._user_update_threshold(users[user_id]))]
    moduli = await asyncio.gather(
        *[self._modulus(user_id=user_id) for user_id in users_to_update])
    users_to_update = [user_id for user_id, modulus in zip(users_to_update, moduli)
                       if modulus == wall_modulus]
    if users_to_update:
      log.info(f'Running automated update for {len(users_to_update)}'
               f' user{nl.s(len(users_to_update))}.')
      await asyncio.gather(
          *[self._update_user(user_id=user_id) for user_id in users_to_update],
          return_exceptions=True)

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
    await ctx.send('%s is affiliated with Team **%s** (%s).' % (
        display(user), self.teams[team_id].display_name, team_id))

  @_team.command(name='register')
  async def team_register(self, ctx: commands.Context, user: discord.User = None):
    """Initiate registration for target user (default: self).

    Any user may call this command on themself (with no argument). Only admins
    may call this command on others."""
    if user is not None:
      if not mod.is_mod_or_superior(self.bot, ctx.author):
        await ctx.add_reaction(u'🙅')
        return
    else:
      user = ctx.author
    await ctx.send(f'Sending registration prompt to {user.display_name}.')
    if not await self.registration_prompt(user, bypass_ignore=True):
      await self.admin_msg(f'Discord registration for {user.name}#{user.discriminator}'
                           ' failed; user may have the bot blocked, or have DMs from non-'
                           'friends disabled.')

  @_team.command(name='update')
  async def team_update(self, ctx: commands.Context, *users: discord.User):
    """Force update of some users team affiliation (default: self).

    Any user may call this command on themself (with no argument). Mods may
    call this targeting any number of users.

    To force an update for an entire team, see `[p]team refresh`."""
    if users:
      if not (await mod.is_mod_or_superior(self.bot, ctx.author)):
        await ctx.add_reaction(u'🙅')
        return
    else:
      users = (ctx.author,)
    if len(users) == 1:
      msg = f'Updating team affiliation for {display(user)}'
    else:
      msg = f'Updating team affiliation for {len(users)} users'
    message = await ctx.send(msg)
    await asyncio.gather(
        *[self._update_user(user=user) for user in users],
        return_exceptions=True)
    await message.edit(content=(message.content + ' ... done'))

  @_team.command(name='refresh')
  @checks.mod_or_permissions(manage_channels=True)
  async def team_refresh(self, ctx: commands.Context, team_id: int):
    """Force update of target team's information.

    `[p]team search` may be useful for finding the number ID of a team.
    """
    if team_id in self.teams and self.teams[team_id] is not None:
      team_data = self.teams[team_id]
      message = await ctx.send(f'Updating team info for team `{team_data.username}`')
      await self._update_team(team=team_data)
    else:
      message = await ctx.send(f'Updating team info for team #{team_id}')
      await self._update_team(team_id=team_id)
    await message.edit(content=(message.content + ' ... done'))

  @_team.command(name='forget')
  async def team_forget(self, ctx: commands.Context, user: discord.User = None):
    """Remove team affiliation from target user (default: self).

    Any user may call this command on themself (with no argument). Only admins
    may call this command on others."""
    if user is not None:
      if not mod.is_mod_or_superior(self.bot, ctx.author):
        await ctx.add_reaction(u'🙅')
        return
    else:
      user = ctx.author
    await self._forget_user(user)
    await ctx.send(f'Okay, I have forgotten all about {display(user)}.')

  @_team.command(name='ignore')
  async def team_ignore(self, ctx: commands.Context):
    """Opt out of automated messages from the team management cog."""
    await self.config.user(ctx.author).do_not_message.set(True)
    await ctx.send('Okay, I won\'t DM about this anymore.')

  @_team.command(name='unignore')
  async def team_unignore(self, ctx: commands.Context):
    """Opt (back) in to automated messages from the team management cog."""
    await self.config.user(ctx.author).do_not_message.set(False)
    await ctx.send('Okay, I\'ll include you back in team-wide DMs.')

  @_team.command(name='search')
  @checks.mod_or_permissions(manage_channels=True)
  async def team_search(self, ctx: commands.Context, username: str):
    """Search for team by username."""
    all_usernames = {team_id: team.username for team_id, team in self.teams.items()
                     if team is not None}
    suggestions = []
    log.info(repr(fuzzywuzzy.process.extract(
        username, all_usernames, limit=5)))
    for fuzz_username, rating, fuzz_id in fuzzywuzzy.process.extract(
        username, all_usernames, limit=5):
      if rating < 50:
        break
      fuzz_team = self.teams[fuzz_id]
      suggestions.append(
          f'(ID: **{fuzz_team.team_id}**) **{fuzz_team.display_name[:40]}**'
          f' -- {len(fuzz_team.users)} registered members')
    if suggestions:
      await ctx.send('\n'.join(suggestions))
    else:
      await ctx.send(f"Couldn't find any teams whose usernames resembled `{username}`")

  @_team.command(name='show')
  @checks.mod_or_permissions(manage_channels=True)
  async def team_show(self, ctx: commands.Context, team_id: int):
    """Show team channels and members."""
    try:
      if team_id not in self.teams:
        self.teams[team_id] = await self._get_team_data(team_id)
      team = self.teams[team_id]
    except KeyError:
      await ctx.send(f'Unrecognized team ID {team_id}. If you think this is a '
                     'valid team ID, perhaps no one from that team has '
                     'registered a Discord account yet.')
      return

    if ctx.guild:
      members, users = self._get_members_if_possible(
          [user.id for user in team.users], ctx.guild)
    else:
      members, users = [], team.users

    pages = paginate_team_data(members, users,
                               [channel for channel in team.channels
                                if channel and channel.guild == ctx.guild])

    embeds = [
        discord.Embed(title=f'**{team.display_name} (ID: {team.team_id})**',
                      color=discord.Color(0x22aaff),
                      description=content)
        for content in pages]
    if len(embeds) == 1:
      await ctx.send(embed=embeds[0])
    else:
      await menu(ctx, embeds, DEFAULT_CONTROLS, timeout=120)

  @_team.command(name='show-all')
  @checks.mod_or_permissions(manage_channels=True)
  async def team_show_all(self, ctx: commands.Context, n: int=3):
    """Show tabulated information on all teams along with up to `n` members from each.

    Team members not in this guild are excluded from consideration. Teams
    with no members in this guild will not show up at all."""
    lines = [team.table_line(ctx.guild, n) for team in self.teams.values()]
    lines = [line for line in lines if line]
    for page in paginate_table(lines):
      await ctx.send(page)

  ######### Admin stuff

  @_team.group(name='admin', invoke_without_command=True)
  @checks.mod_or_permissions(manage_channels=True)
  async def _admin(self, ctx: commands.Context):
    """Team affiliation administrator functions.

    **Global setup**: Use `[p]team admin server_url <url>` and `[p]team admin secret <token>` to set parameters for reading team data. Set `[p]team admin stderr <channel>` to determine where admin-level stderr messages go; all such messages are transmitted to every stderr channel in every server.

    **Per-server setup**: Set `[p]team admin stderr <channel>` if you want admin messages to appear in this server. Set `[p]set addmodrole <role name>` to allow that role to use `team admin`-level commands. `[p]team admin enable` to begin watching for incoming team members.

    **Running an event**: Run `[p]team admin select <n>` to select `n` people from each team as Participant. Then `[p]team channel auto-batch <channel_type> <k>` to create channels containing `k` teams each. `channel_type` is `text`, `voice`, or `both`.
    """
    await ctx.send_help()

  @_admin.command(name='omg-omg-wtf-reset')
  @checks.mod_or_permissions(manage_channels=True)
  async def admin_reset(self, ctx: commands.Context):
    """Resets all team management data, GLOBALLY."""
    await self.config.clear_all()
    await self.initialize_internals()
    await ctx.send('Global team management factory reset complete.')

  @_admin.command(name='select')
  @checks.mod_or_permissions(manage_channels=True)
  async def admin_select(self, ctx: commands.Context, count: int=1):
    """Selects up to `count` members from each team to be Participants.

    Note that this also deselects other members from being Participants. That
    is, running this command twice will not result in more than the desired
    number of participants per team.
    """
    participant = await self._get_or_create_participant_role(ctx.guild)
    user_count, team_count = 0, 0
    p_removed, p_added = 0, 0
    for team in self.teams.values():
      users_here = team.users_here(ctx.guild)
      if not users_here:
        continue
      ps, qs = [], []
      for member in users_here:
        if participant in member.roles:
          ps.append(member)
        else:
          qs.append(member)

      if count < len(ps):
        random.shuffle(ps)
        await asyncio.gather(*[member.remove_roles(participant)
                               for member in ps[count:]],
                             return_exceptions=True)
        p_removed += (len(ps) - count)
      elif count > len(ps):
        random.shuffle(qs)
        await asyncio.gather(*[member.add_roles(participant)
                               for member in qs[:(count - len(ps))]],
                             return_exceptions=True)
        p_added += min(len(qs), count - len(ps))
      team_count += 1
    await ctx.send(f'Selected {user_count} participant{nl.s(user_count)}'
                   f' across {team_count} team{nl.s(team_count)}.')

  @_admin.command(name='enable')
  @commands.guild_only()
  @checks.mod_or_permissions(manage_channels=True)
  async def admin_enable(self, ctx: commands.Context):
    """Enable team management in this server."""
    if ctx.guild.id in self.guilds:
      await ctx.send('Team management is already enabled in this guild.')
      return
    await self._enable_guild(guild=ctx.guild)
    await ctx.send('Team management enabled.')

  @_admin.command(name='disable')
  @commands.guild_only()
  @checks.mod_or_permissions(manage_channels=True)
  async def admin_disable(self, ctx: commands.Context):
    """Disable team management in this server."""
    if ctx.guild.id not in self.guilds:
      await ctx.send('Team management is already disabled in this guild.')
      return
    await self._disable_guild(guild=ctx.guild)
    await ctx.send('Team management disabled.')

  @_team.group(name='channel', invoke_without_command=True)
  @commands.guild_only()
  @checks.mod_or_permissions(manage_channels=True)
  async def _channel(self, ctx: commands.Context, channel_type: str, *team_ids: int):
    """Create a text and/or voice channel only visible to certain teams.

    `channel_type` should be one of `text`, `voice`, or `both`. `[p]team search`
    may be useful for finding the number ID of a team.
    """
    if set(team_ids) - set(self.teams):
      await ctx.send('Missing data for the following team IDs: %s' % (
          ', '.join(map(str, set(team_ids) - set(self.teams))),))
      return

    if channel_type not in ['text', 'voice', 'both']:
      await ctx.send(f'Received unexpected channel type `{channel_type}`;'
                     ' use `text`, `voice`, or `both`.')
      return

    channel_name = random_channel_name()
    channels = []
    if channel_type in ['text', 'both']:
      channels.append(await self._create_team_text_channel(
          channel_name, ctx.guild, *[self.teams[team_id] for team_id in team_ids]))
    if channel_type in ['voice', 'both']:
      channels.append(await self._create_team_voice_channel(
          channel_name, ctx.guild, *[self.teams[team_id] for team_id in team_ids]))

    await ctx.send('Created channel %s for team%s `%s`' % (
        channels[0].mention,
        nl.s(len(team_ids)),
        '`, `'.join(self.teams[team_id].username for team_id in team_ids)))

  @_channel.command(name='batch')
  @commands.guild_only()
  @checks.mod_or_permissions(manage_channels=True)
  async def channel_batch(
      self, ctx: commands.Context, channel_type: str, *args):
    """Batch create text and/or voice channels for certain team groups.

    `channel_type` should be one of `text`, `voice`, or `both`.

    Format your arguments with spaces separating team IDs, and pipes `|`
    separating groups of team IDs. Empty groups are valid. For example, the
    command

    `[p]team channel batch text | 3 5 | 1 | 2 | |`

    will create 6 text channels, and the first, fifth, and sixth will not have
    any teams assigned to them. It is equivalent to running

    `[p]team channel text`
    `[p]team channel text 3 5`
    `[p]team channel text 1`
    `[p]team channel text 2`
    `[p]team channel text`
    `[p]team channel text`
    """
    team_groups, bad_args = [[]], []
    if channel_type not in ['text', 'voice', 'both']:
      bad_args.append(channel_type)
    for arg in args:
      if arg == '|':
        team_groups.append([])
      elif not arg.isdigit() or int(arg) not in self.teams:
        bad_args.append(arg)
      else:
        team_groups[-1].append(arg)

    if bad_args:
      await ctx.send(
          f'Received invalid arguments for batch channel creation: {bad_args}')
      return

    fake_msg = copy.copy(ctx.message)
    new_cmd = (await self._prefix()) + ctx.command.full_parent_name
    for group in team_groups:
      fake_msg.content = '%s %s %s' % (new_cmd, channel_type, ' '.join(group))
      new_ctx = await self.bot.get_context(fake_msg)
      await self.bot.invoke(new_ctx)

  @_channel.command(name='auto-batch')
  @commands.guild_only()
  @checks.mod_or_permissions(manage_channels=True)
  async def channel_auto_batch(
      self, ctx: commands.Context, channel_type: str, group_size: int=1):
    """Automatically batch create channels with about `group_size` teams per channel.

    Determines the number of desired channels based on `group_size`, then
    partitions teams into that many channels in a balanced way. For example, if
    there are 78 teams and a `group_size` of 50, the result is two channels with
    39 teams each, *not* a channel with 50 teams and a channel with 28.

    This is equivalent to running a very long `[p]team channel batch`
    command except you don't need to figure out what the arguments are."""
    if channel_type not in ['text', 'voice', 'both']:
      await ctx.send(f'`{channel_type}` is not a valid channel type.')
      return

    teams = [team_data for team_data in self.teams.values()
             if team_data.users_here(ctx.guild)]
    random.shuffle(teams)
    num_channels = round(len(teams) / group_size + .4999)
    groups = []
    for i in range(0, num_channels):
      groups.append([
          team.team_id for team in
          teams[round(i * len(teams) / num_channels):
                round((i + 1) * len(teams) / num_channels)]])

    fake_msg = copy.copy(ctx.message)
    new_cmd = (await self._prefix()) + ctx.command.full_parent_name
    for group in groups:
      fake_msg.content = '%s %s %s' % (
          new_cmd, channel_type, ' '.join(map(str, group)))
      new_ctx = await self.bot.get_context(fake_msg)
      await self.bot.invoke(new_ctx)

  @_channel.command(name='add')
  @commands.guild_only()
  @checks.mod_or_permissions(manage_channels=True)
  async def channel_add(
      self, ctx: commands.Context, channel: discord.abc.GuildChannel, *team_ids: int):
    """Add visibility to target channel for certain teams.

    Note that voice channels must be identified by ID number. This can be
    obtained by right clicking on the voice channel and selecting 'Copy ID'; you
    may need to have developer options enabled.

    `[p]team search` may be useful for finding the number ID of a team.
    """
    if set(team_ids) - set(self.teams):
      await ctx.send('Missing data for the following team IDs: %s' % (
          ', '.join(map(str, set(team_ids) - set(self.teams))),))
      return

    await asyncio.gather(*[
        self._permit_team_in_channel(self.teams[team_id], channel)
        for team_id in team_ids],
                         return_exceptions=True)
    await ctx.send('Added team%s `%s` to channel %s' % (
        nl.s(len(team_ids)),
        '`, `'.join(self.teams[team_id].username for team_id in team_ids),
        channel.mention))

  @_channel.command(name='remove')
  @commands.guild_only()
  @checks.mod_or_permissions(manage_channels=True)
  async def channel_remove(
      self, ctx: commands.Context, channel: discord.abc.GuildChannel, *team_ids: int):
    """Remove visibility to target channel for certain teams.

    Note that voice channels must be identified by ID number. This can be
    obtained by right clicking on the voice channel and selecting 'Copy ID'; you
    may need to have developer options enabled.

    `[p]team search` may be useful for finding the number ID of a team.
    """
    if set(team_ids) - set(self.teams):
      await ctx.send('Missing data for the following team IDs: %s' % (
          ', '.join(map(str, set(team_ids) - set(self.teams))),))
      return

    await asyncio.gather(*[
        self._forbid_team_in_channel(self.teams[team_id], channel)
        for team_id in team_ids],
                         return_exceptions=True)
    await ctx.send('Removed team%s `%s` from channel %s' % (
        nl.s(len(team_ids)),
        '`, `'.join(self.teams[team_id].username for team_id in team_ids),
        channel.mention))

  @_admin.command(name='stderr')
  @commands.guild_only()
  @checks.mod_or_permissions(manage_channels=True)
  async def admin_stderr(
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
        others = len([ch for ch in self.admin_channels.values() if ch]) - 1
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
  @checks.mod_or_permissions(manage_channels=True)
  async def admin_local_ping(self, ctx: commands.Context,
                             team_identifier: str, *message):
    """Ping all members of the indicated team, globally, via DM.

    If you need to only ping local members of a team, consider first setting up
    a channel only they can see with `[p]team channel text <team_id>` and use
    at-here to get their attention.
    """

    pass

  @_admin.command(name='ping-all')
  @checks.mod_or_permissions(manage_channels=True)
  async def admin_global_ping(self, ctx: commands.Context,
                              team_identifier: str, *message):
    """Ping all members of the indicated team, globally.

    This issues pings to the indicated members *via DM*. Team members that have
    opted out using `[p]team ignore` will not receive the ping.
    """
    pass

  @_admin.command(name='secret')
  @checks.mod_or_permissions(manage_channels=True)
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

  @_admin.command(name='server_url')
  @checks.mod_or_permissions(manage_channels=True)
  async def admin_server_url(self, ctx: commands.Context, *url: str):
    """Sets or displays the URL used for retrieving details from silenda."""
    the_url = await self.config.server_url()
    url = ' '.join(url)
    if not url:
      await ctx.author.send(f'Team management server url: {the_url}')
    else:
      await self.config.server_url.set(url)
      message = [display(ctx.author),
                 f'set the team management server url to {url}.']
      if the_url:
        message.append(f'(was `{the_url}`)')
      await self.admin_msg(' '.join(message))

  async def admin_msg(self, message):
    """Sends a message to all admin channels."""
    for channel in self.admin_channels.values():
      if channel:
        await channel.send(message)

  ######## Data management helper functions/utilities

  ## These are here to make sure that writes to the cog's internal memory are
  ## reflected to the datastore, and the reads from the cog's datastore are
  ## reflected to its internal memory.

  async def _prefix(self):
    return (await self.bot._prefix_cache.get_prefixes())[0]

  async def _modulus(self, user: discord.User = None, user_id: int = None):
    if not user:
      user = self.bot.get_user(user_id)
    modulus = await self.config.user(user).refresh_modulus()
    if modulus == -1:
      modulus = random.randint(0, 6)
      await self.config.user(user).refresh_modulus.set(modulus)
    return modulus

  async def _user_update_threshold(self, user_config: dict):
    """Minimum number of seconds after which a user update should be requested."""
    return 30.0 * user_config['backoff_factor']

  async def _increment_user_backoff(self, user: discord.User):
    team_id = await self.config.user(user).team_id()
    backoff = await self.config.user(user).backoff_factor()

    if team_id == -1:
      await self.config.user(user).backoff_factor.set(
          min(backoff_factor * 1.2, 10))
    else:
      await self.config.user(user).backoff_factor.set(
          min(backoff_factor * 1.2, 40))

  async def _token(self, user: discord.User = None, user_id: int = None):
    """Get a secret token for the user."""
    # This is to be used with the registration URL so that it doesn't contain
    # the user's ID in cleartext. This is so that person A cannot trivially
    # generate person B's URL and assign them to person A's team.
    if not user:
      user = self.bot.get_user(user_id)
    hashh = await self.config.user(user).digest()
    if hashh is None:
      salt = await self.config.user(user).secret()
      if salt is None:
        salt = random_salt()
        await self.config.user(user).secret.set(salt)
      hashh = digest(user.id, salt)
      await self.config.user(user).digest.set(hashh)
      await self.config.set_raw('undigest', hashh, value=user.id)
    return hashh

  async def _user_id_from_digest(self, hashh):
    return await self.config.get_raw('undigest', hashh, default=None)

  async def _register_url(self):
    url = await self.config.server_url()
    if not url:
      prefix = await self._prefix()
      await self.admin_msg(
          'Team server URL is not set. Use '
          f'`{prefix}team admin server_url <url>` to set it.')
      raise MissingCogSettingException('server_url is unset')
    return os.path.join(url, 'register_discord')

  async def _update_url(self):
    url = await self.config.server_url()
    if not url:
      prefix = await self._prefix()
      await self.admin_msg(
          'Team server URL is not set. Use '
          f'`{prefix}team admin server_url <url>` to set it.')
      raise MissingCogSettingException('server_url is unset')
    return os.path.join(url, 'lookup_discord')

  async def _load_guild(self,
                        guild: discord.Guild = None,
                        guild_id: int = None):
    if guild is None:
      guild = self.bot.get_guild(guild_id)
      if guild is None:
        log.warning(f'Invalid guild ID {guild_id}; removing from config.')
        await self.config.guild_from_id(guild_id).clear_raw()
        return
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

  async def _get_team_data(self, team_id):
    # Raises KeyError from get_raw if team_id is not a recognized team ID
    data = await self.config.get_raw('teams', team_id)
    if data is None:
      return None
    return await TeamData.read(self.bot, data)

  async def _update_user(self, user: discord.User = None, user_id: int = None):
    if user is None:
      user = self.bot.get_user(user_id)
    original_team_id = await self.config.user(user).team_id()
    backoff_factor = await self.config.user(user).backoff_factor()

    url = await self._update_url()
    params = {
        'auth': await self.config.secret(),
        'user_id': await self._token(user),
    }

    response = requests.get(url, params=params)
    if response.status_code != 200:
      await self.admin_msg(
          'Attempt to refresh user data failed with error'
          f' {response.status_code}: {response.text}')
      return
    data = response.json()

    if not data['success']:
      # User ID is completely unknown to hunt DB. Don't update last_updated so
      # that this user might get picked on the next go
      log.warning(f'Attempt to get team affiliation for {display(user)}'
                  f' failed at URL {response.url}')
      await self._increment_user_backoff(user)
      return

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

    if original_team_id != team_id:
      old_team = self.teams.get(await self.config.user(user).team_id(), None)
      if old_team:
        await self._remove_user_from_team(user, old_team)
      await self._add_user_to_team(user, team_data)
    else:
      await self._increment_user_backoff(user)
      await self.config.user(user).last_updated.set(time.time())

  async def _forget_user(self, user: discord.User = None, user_id: int = None):
    if user is None:
      user = self.bot.get_user(user_id)

    url = await self._removal_url()
    params = {
        'auth': await self.config.secret(),
        'user_id': await self._token(user),
    }

    response = requests.get(url, params=params)
    if response.status_code != 200:
      await self.admin_msg(
          'Attempt to refresh user data failed with error'
          f' {response.status_code}: {response.text}')
      return
    data = response.json()

    if not data['success']:
      # User ID is completely unknown to hunt DB. Don't update last_updated so
      # that this user might get picked on the next go
      log.warning(f'Attempt to remove user data for {display(user)}'
                  f' failed at URL {response.url}')
      return

    team = self.teams.get(await self.config.user(user).team_id(), None)
    if team is not None:
      await self._remove_user_from_team(user, team)
    await self.config.user(user).clear()


  async def _update_team(self, team: TeamData = None, team_id: int = None):
    if team is None:
      if team_id not in self.teams:
        try:
          self.teams[team_id] = await self._get_team_data(team_id)
        except KeyError:
          self.teams[team_id] = None
      team = self.teams[team_id]
    params = {
        'auth': await self.config.secret(),
        'team_id': team_id or team.team_id,
    }

    response = requests.get(await self._update_url(), params=params)
    if response.status_code != 200:
      await self.admin_msg(
          f'Attempt to refresh team data for {params["team_id"]} failed with '
          f'error {response.status_code}: {response.text}')
      del self.teams[params['team_id']]
      return
    data = response.json()
    log.info(f'Got data for team {params["team_id"]}: {data}')
    if not data['success']:
      await self.admin_msg(
          f'Attempt to refresh team data for {params["team_id"]} failed;'
          ' there might not be any Discord accounts bound to it.')
      del self.teams[params['team_id']]
      return

    if team is None:
      team_data = await TeamData.read(self.bot, {
          'team_id': data['team'][0],
          'display_name': data['team'][2],
          'username': data['team'][3]
      })
      await team_data.write(self.config)
      self.teams[team_id] = team_data
      team = team_data

    original_users = set(user.id for user in team.users)
    updated_users = set()
    for user_hash in data['user_ids']:
      updated_users.add(await self._user_id_from_digest(user_hash))
    updated_users.discard(None)
    ids_to_add = list(updated_users - original_users)
    users_to_add = [self.bot.get_user(user_id) for user_id in ids_to_add]
    ids_to_remove = list(original_users - updated_users)
    users_to_remove = [self.bot.get_user(user_id) for user_id in ids_to_remove]
    ids_to_backoff = list(original_users & updated_users)
    users_to_backoff = [self.bot.get_user(user_id) for user_id in ids_to_backoff]

    await asyncio.gather(
        *[self._increment_user_backoff(user) for user in users_to_backoff],
        return_exceptions=True)

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

  async def _add_user_to_team(self, user: discord.User, team: TeamData):
    if user in team.users:
      log.debug(f'User {display(user)} is already on {team.username}.')
      return
    team.users.append(user)
    await team.write(self.config)

    await self.config.user(user).team_id.set(team.team_id)
    await self.config.user(user).backoff_factor.set(4.0)
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

    old_digest = await self.config.user(user).digest()
    async with self.config.undigest() as undigest:
      if old_digest in undigest:
        del undigest[old_digest]
    await self.config.user(user).team_id.set(-1)
    await self.config.user(user).secret.set(None)
    await self.config.user(user).digest.set(None)
    await self.config.user(user).backoff_factor.set(2.0)
    await self.config.user(user).last_updated.set(time.time())

    for channel in team.channels:
      await self._forbid_user_in_channel(
          user, channel, f'Removing {user.name} from {team.username}')

  async def _get_or_create_team_category(self, guild: discord.Guild):
    category_id = await self.config.guild(guild).teams_category()
    if category_id is None or guild.get_channel(category_id) is None:
      category = await guild.create_category('Team Channels')
      await self.config.guild(guild).teams_category.set(category.id)
    else:
      category = guild.get_channel(category_id)
    return category

  async def _get_or_create_participant_role(self, guild: discord.Guild):
    all_roles = await guild.fetch_roles()
    for role in all_roles:
      if role.name == PARTICIPANT_ROLE_NAME:
        return role
        break
    else:
      try:
        return await guild.create_role(
            name=PARTICIPANT_ROLE_NAME,
            color=discord.Color.gold(),
            mentionable=True,
            reason='Automated role creation for Participant labelling')
      except discord.Forbidden:
        await self.admin_msg(
            f'Could not automatically create **{PARTICIPANT_ROLE_NAME}**'
            f'role in {guild.name}.')
        raise


  async def _create_team_text_channel(
      self, name: str, guild: discord.Guild, *teams: TeamData):
    team_category = await self._get_or_create_team_category(guild)
    participant_role = await self._get_or_create_participant_role(guild)
    permission_overwrites = {
        guild.default_role: DEFAULT_PERM,  # none
        guild.get_member(self.bot.user.id): MOD_PERM,  # all
        participant_role: PARTICIPANT_PERM,
    }
    mod_roles = [guild.get_role(role_id)
                 for role_id in await self.bot._config.guild(guild).mod_role()]
    for role in mod_roles:
      permission_overwrites[role] = MOD_PERM

    channel = await team_category.create_text_channel(
        name, overwrites=permission_overwrites)
    await asyncio.gather(
        *[self._permit_team_in_channel(team, channel) for team in teams],
        return_exceptions=True)
    return channel

  async def _create_team_voice_channel(
      self, name: str, guild: discord.Guild, *teams: TeamData):
    team_category = await self._get_or_create_team_category(guild)
    participant_role = await self._get_or_create_participant_role(guild)
    permission_overwrites = {
        guild.default_role: DEFAULT_PERM,  # none
        guild.get_member(self.bot.user.id): MOD_PERM,  # all
        participant_role: PARTICIPANT_PERM,
    }
    mod_roles = [guild.get_role(role_id)
                 for role_id in await self.bot._config.guild(guild).mod_role()]
    for role in mod_roles:
      permission_overwrites[role] = MOD_PERM

    channel = await team_category.create_voice_channel(
        name, overwrites=permission_overwrites)
    await asyncio.gather(
        *[self._permit_team_in_channel(team, channel) for team in teams],
        return_exceptions=True)
    return channel

  ## NOTE: Channels will be created such that users have all the necessary
  ## permissions EXCEPT being able to see the channel. This means that
  ## general user permissions on all channels is to be gated solely on
  ## visibility and no other permission.

  def _get_members_if_possible(self, user_ids, guild: discord.Guild):
    """Given a list of user_ids, returns a list of Members for those users
    present in the `guild`, and a list of Users for those users who aren't."""
    members, users = [], []
    for user_id in user_ids:
      member = guild.get_member(user_id)
      if member:
        members.append(member)
      else:
        users.append(self.bot.get_user(user_id))
    return members, users

  async def _permit_user_in_channel(self, user: discord.User,
                                    channel: discord.abc.GuildChannel,
                                    reason: str = None):
    guild = channel.guild
    member = guild.get_member(user.id)
    if member is None:
      # User is not in the guild containing the channel
      return
    await self._permit_member_in_channel(member, channel, reason=reason)


  async def _permit_member_in_channel(self, member: discord.Member,
                                      channel: discord.abc.GuildChannel,
                                      reason: str = None):
    try:
      await channel.set_permissions(member, overwrite=TEAMMATE_PERM,
                                    reason=reason)
    except Exception as e:
      log.error(str(e))
      log.error("DEAD")


  async def _forbid_user_in_channel(self, user: discord.User,
                                    channel: discord.abc.GuildChannel,
                                    reason: str = None):
    guild = channel.guild
    member = guild.get_member(user.id)
    if member is None:
      # User is not in the guild containing the channel
      return
    await self._forbid_member_in_channel(member, channel, reason=reason)

  async def _forbid_member_in_channel(self, member: discord.Member,
                                      channel: discord.abc.GuildChannel,
                                      reason: str = None):
    await channel.set_permissions(member, overwrite=DEFAULT_PERM,
                                  reason=reason)

  async def _permit_team_in_channel(self, team: TeamData,
                                    channel: discord.abc.GuildChannel):
    if channel in team.channels:
      return
    team.channels.append(channel)
    await team.write(self.config)

    await asyncio.gather(*[
        self._permit_user_in_channel(
            channel.guild.get_member(user.id),
            channel, f'Adding {team.username}')
        for user in team.users
    ])

  async def _forbid_team_in_channel(self, team: TeamData,
                                    channel: discord.abc.GuildChannel):
    if channel not in team.channels:
      return
    team.channels.remove(channel)
    await team.write(self.config)

    await asyncio.gather(*[
        self._forbid_user_in_channel(user, channel, f'Removing {team.username}')
        for user in team.users])

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
    users = await self.config.all_users()
    for user_id in users:
      await self.config.user_from_id(user_id).last_updated.set(0)
    await ctx.send(await self.config.user(ctx.author).last_updated())


class MissingCogSettingException(Exception):
  pass
