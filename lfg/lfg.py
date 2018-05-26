"""Cog for tracking players looking to play certain games."""

import asyncio
import collections
import heapq
import itertools
import logging
import time

import discord
from discord.ext import commands

from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red


log = logging.getLogger('lfg')


## TODO: Create a default empty-string group for use cases where one LFG queue
## is sufficient, so that the cog can be used with zero additional configuration

## TODO: Refactor queue name validation to happen at get-time

## TODO: Refactor queue-not-found errors to return early to improve nesting
## readability


class NoSuchQueueError(Exception):
  pass


class GuildQueue:

  REMOVED = '<removed-member>'

  def __init__(self, name, role, default_time):
    self.name = name.lower()
    self.dname = name                 # display name
    self.role = role                  # discord.py Role object
    self.default_time = default_time  # time to wait in queue, in minutes

    self.queue = []  # maintain using heapq
    self.finder = {}
    self.id_count = itertools.count()

  def __contains__(self, member):
    return member in self.finder

  def __len__(self):
    return len(self.queue)

  def Clear(self):
    self.queue = []
    self.finder = {}

  def AddMember(self, member, wait_time=None):
    ## Returns True if the member wasn't in the queue already
    new_member = True
    wait_time = wait_time or self.default_time
    if member in self.finder:
      self.RemoveMember(member)
      new_member = False
    count = next(self.id_count)
    queued_member = [int(time.time()) + wait_time * 60,
                     count, member]
    self.finder[member] = queued_member
    heapq.heappush(self.queue, queued_member)
    return new_member

  def RemoveMember(self, member):
    queued_member = self.finder.pop(member)
    queued_member[-1] = GuildQueue.REMOVED

  def PopMember(self):
    member = heapq.heappop(self.queue)[2]
    del self.finder[member]
    return member

  def ListMembers(self):
    return list(self.finder)

  def Overdue(self):
    while self.queue and self.queue[0][2] == GuildQueue.REMOVED:
      heapq.heappop(self.queue)
    return (time.time() > self.queue[0][0]) if self.queue else False


def is_person_text(number, verb=True):
  if number == 1:
    return 'is 1 person' if verb else '1 person'
  else:
    return ('are %d people' if verb else '%d people') % number


class Lfg:

  default_guild_settings = {
    'queues': {},
  }

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, 0x45B277A910C8D1E5, force_registration=True)
    self.config.register_guild(**self.default_guild_settings)

    ## TODO: Initialize this queue state on startup
    self.guild_queues = collections.defaultdict(dict)
    self.monitoring = {}
    self.watch_interval = 60  # seconds

  def __unload(self):
    for guild_id in self.monitoring:
      self.monitoring[guild_id] = False

  ####### Internal accessors

  async def get_queue_data(self, guild, queue_name):
    """Returns a dictionary containing the config data for the QUEUE_NAME queue in GUILD.

    Currently, this return value has keys 'name', 'role_id', 'mention', and 'default_time'."""
    queue_data = await self.config.guild(guild).get_raw(
        'queues', queue_name.lower(), default=None)
    if queue_data is None:
      raise NoSuchQueueError
    return queue_data

  async def set_queue_data(self, guild, queue_name, datum):
    """Sets the QUEUE_NAME queue in GUILD to have config DATUM."""
    await self.config.guild(guild).set_raw(
        'queues', queue_name.lower(), value=datum)

  async def add_to_queue(self, queue, person, minutes):
    await person.add_roles(queue.role)
    return queue.AddMember(person, minutes)

  async def pop_from_queue(self, queue):
    person = queue.PopMember()
    await person.remove_roles(queue.role)
    return person

  async def remove_from_queue(self, queue, person):
    queue.RemoveMember(person)
    await person.remove_roles(queue.role)

  async def remove_from_all_queues(self, person, guild):
    queues = []
    for queue in self.guild_queues[guild.id].values():
      if person in queue:
        queues.append(queue.name)
        await self.remove_from_queue(queue, person)
    return queues

  ####### Commands

  @commands.group(name='queue', invoke_without_command=True)
  async def _queue(self, ctx: commands.Context):
    """LFG queue management functions.

    To actually join or leave queues, see the `lfg` command group."""
    await ctx.send_help()

  @_queue.command(name='load')
  @commands.guild_only()
  @checks.admin()
  async def queue_load(self, ctx: commands.Context):  ## !queue load
    """Load queue configs for this guild."""
    guild_queues = {}
    async with self.config.guild(ctx.guild).queues() as queues:
      for queue_name, queue_config in queues.items():
        if queue_config is None:  # queue may have existed before but was deleted
          continue
        guild_queues[queue_config['name'].lower()] = GuildQueue(
            name=queue_config['name'],
            role=discord.utils.get(ctx.guild.roles, id=queue_config['role_id']),
            default_time=queue_config['default_time'])
    self.guild_queues[ctx.guild.id].update(guild_queues)
    await ctx.send('Loaded %d queue configurations: %s' % (
      len(guild_queues), ', '.join('`%s`' % queue_name for queue_name in guild_queues)))

  @_queue.command(name='create')
  @commands.guild_only()
  @checks.admin()
  async def queue_create(self, ctx: commands.Context, name):  ## !queue create
    """Create a new queue."""
    if name.lower() in self.guild_queues[ctx.guild.id]:
      await ctx.send('Sorry, it looks like there\'s already a queue in place for %s.' % name)
    else:
      new_role = await ctx.guild.create_role(name='LFG %s' % name)
      self.guild_queues[ctx.guild.id][name.lower()] = GuildQueue(
          name=name, role=new_role, default_time=60)
      config_datum = {
        'name': name,
        'role_id': new_role.id,
        'default_time': 60,  # minutes = 1 hour
      }
      await self.config.guild(ctx.guild).set_raw(
          'queues', name.lower(), value=config_datum)
      await new_role.edit(mentionable=True, position=1)
      await ctx.send('Created new queue `%s` with role %s' % (name.lower(), new_role.mention))

  @_queue.command(name='settime')
  @commands.guild_only()
  @checks.admin()
  async def queue_settime(self, ctx: commands.Context, name, wait_time):
    """Set the default wait time for a queue."""
    if name.lower() not in self.guild_queues[ctx.guild.id]:
      await ctx.send('I don\'t recognize any queue `%s`.' % name.lower())
    else:
      self.guild_queues[ctx.guild.id][name.lower()].default_time = wait_time
      await self.config.guild(ctx.guild).set_raw(
          'queues', name.lower(), 'default_time', value=wait_time)
      await ctx.send('Set queue `%s` to have default wait time %d minutes.' % (
          name.lower(), wait_time))

  @_queue.command(name='list')
  @commands.guild_only()
  async def queue_list(self, ctx: commands.Context):  ## !queue list
    """List available LFG queues."""
    queues = self.guild_queues[ctx.guild.id]
    if not queues:
      await ctx.send('There don\'t appear to be any LFG queues you can join...')
    else:
      await ctx.send('There %s %d queue%s you can join:\n    `%s`' % (
        'is' if len(queues) == 1 else 'are', len(queues),
        's' if len(queues) > 1 else '', '`, `'.join(queues)))

  @_queue.command(name='delete')
  @commands.guild_only()
  @checks.admin()
  async def queue_delete(self, ctx: commands.Context, name):  ## !queue delete
    """Remove a queue."""
    if name.lower() not in self.guild_queues[ctx.guild.id]:
      await ctx.send('Sorry, there doesn\'t appear to be a queue by that name.')
    else:
      await self.guild_queues[ctx.guild.id][name.lower()].role.delete()
      await self.config.guild(ctx.guild).set_raw('queues', name.lower(), value=None)
      del self.guild_queues[ctx.guild.id][name.lower()]
      await ctx.send('OK, removed the queue for `%s` and its role.' % name.lower())

  @_queue.command(name='start')
  @commands.guild_only()
  @checks.admin()
  async def queue_start(self, ctx: commands.Context):  ## !queue start
    """Start the background process for queue monitoring in the current guild."""
    await ctx.send('Okay, starting queue monitoring.')
    self.monitoring[ctx.guild.id] = True
    while self.monitoring[ctx.guild.id]:
      for queue in self.guild_queues[ctx.guild.id].values():
        while queue.Overdue():
          member = await self.pop_from_queue(queue)
          await ctx.send(
              '%s has stopped waiting in the `%s` queue due to timeout.' % (
                  member.mention, queue.name))
      await asyncio.sleep(self.watch_interval)

  @_queue.command(name='stop')
  @commands.guild_only()
  @checks.admin()
  async def queue_stop(self, ctx: commands.Context):  ## !queue stop
    """Stop the background process for queue monitoring in the current guild."""
    await ctx.send('Okay, stopping queue monitoring for this guild.')
    self.monitoring[ctx.guild.id] = False

  @commands.group(name='lfg', invoke_without_command=True)
  @commands.guild_only()
  async def _lfg(self, ctx: commands.Context, queue_name, minutes=0):  ## !lfg
    """Join an LFG queue."""
    queue = self.guild_queues[ctx.guild.id].get(queue_name.lower(), None)
    if queue is None:
      await ctx.send('Sorry, there doesn\'t appear to be an LFG queue for that.')
    else:
      ## AddMember side-effects to enqueue ctx.author. The if statement is to handle
      ## the behavior afterward depending on whether
      if await self.add_to_queue(queue, ctx.author, minutes):
        await ctx.send('%s has joined the %s queue (%d %s waiting)' % (
            ctx.author.mention, queue.role.mention, len(queue),
            is_person_text(len(queue), verb=False)))
      else:
        await ctx.send('Okay, updating your time in the %s queue to %d minutes.' % (
            queue.name, minutes))

  @_lfg.command(name='clear')
  @commands.guild_only()
  async def lfg_clear(self, ctx: commands.Context):
    """Remove yourself from all LFG queues in this server."""
    queues = await self.remove_from_all_queues(ctx.author, ctx.guild)
    if queues:
      await ctx.send('Okay, removed you from %d queue%s: %s' % (
          len(queues), 's' if len(queues) > 1 else '',
          ', '.join('`%s`' % name for name in queues)))
    else:
      await ctx.send('You weren\'t in any queues.')

  @_lfg.command(name='list')
  @commands.guild_only()
  async def lfg_list(self, ctx: commands:Context, queue_name=None):
    if queue_name is not None:
      queue = self.guild_queues[ctx.guild.id].get(queue_name.lower(), None)
      if queue in None:
        return await ctx.send('Sorry, there doesn\'t appear to be an LFG queue for that.')
      if len(queue) == 0:
        return await ctx.send('No one\'s currently waiting in the `%s` queue.' % queue.name)
      return await ctx.send(
        'There %s waiting in the `%s` queue:\n%s' % (
          is_person_text(len(queue), verb=True), queue.name,
          '; '.join(member.display_name for member in queue.ListMembers()))
