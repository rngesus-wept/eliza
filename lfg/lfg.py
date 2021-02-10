"""Cog for tracking players looking to play certain games."""

import asyncio
import collections
import heapq
import itertools
import logging
import random
import time

import discord
from redbot.core import commands

from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red


log = logging.getLogger('red.eliza.lfg')


## TODO: Create a default empty-string group for use cases where one LFG queue
## is sufficient, so that the cog can be used with zero additional configuration

## TODO: Refactor queue name validation to happen at get-time

## TODO: Refactor queue-not-found errors to return early to improve nesting
## readability


class NoSuchQueueError(Exception):
  pass


class GuildQueue:
  """Timeout-based priority queue."""

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
    return len(self.finder)

  def __bool__(self):
    return bool(self.finder)

  def Clear(self):
    self.queue = []
    self.finder = {}

  def AddMember(self, member, wait_time=None):
    """Add a MEMBER to this queue for WAIT_TIME minutes.

    Args:
      member - A discord.Member.
      wait_time - The number of minutes the member should remain in queue. (default:
          self.default_time)
    Return:
      True if the member is new to the queue; otherwise False."""
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


def PersonNL(number, verb=True):
  """Correctly conjugates "$VERB $NUMBER person/people"."""
  if number == 1:
    return 'is 1 person' if verb else '1 person'
  return ('are %d people' if verb else '%d people') % number


class Lfg(commands.Cog):
  """Red cog for managing LFG queues."""

  default_guild_settings = {
      'queues': {},
      'lfg_channel': None,
  }

  default_member_settings = {
      'alert': False,
  }

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, 0x45B277A910C8D1E5, force_registration=True)
    self.config.register_guild(**self.default_guild_settings)
    self.config.register_member(**self.default_member_settings)

    self.guild_queues = collections.defaultdict(dict)
    self.monitoring = {}
    self.watch_interval = 60  # seconds

    # Attributes for delaying other initialization until after cog load
    self._ready = asyncio.Event()
    self._init_task = None

    # False if everything is okay
    # Otherwise, the time.time() at which initalization last failed
    self._ready_raised = False


  def create_init_task(self):
    def _done_callback(task):
      exc = task.exception()
      if exc is not None:
        log.error(
          'An unexpected error occurred during Lfg initialization.',
          exc_info=exc)
        self._ready_raised = time.time()
      self._ready.set()

    self._init_task = asyncio.create_task(self.initialize())
    self._init_task.add_done_callback(_done_callback)

  async def initialize(self):
    await self.bot.wait_until_ready()
    guild_ids = list(await self.config.all_guilds())
    results = await asyncio.gather(
      *[self.initialize_guild(guild_id) for guild_id in guild_ids],
      return_exceptions=True)
    successes = results.count(True)
    log.info(f'Monitoring LFG queues in {successes} guilds'
             f' ({len(results) - successes} failures)')
    return all(results)

  async def initialize_guild(self, guild_id: int):
    if self.guild_queues.get(guild_id, None):
      log.info(f'Skipping re-initialization for guild {guild_id}')
      return True
    for retry in range(3):  # retry loop
      try:
        guild = await self.bot.fetch_guild(guild_id)
        break
      except AttributeError:
        # expecting 'NoneType' object has no attribute 'request'
        if retry == 2:
          log.error(f'Failed to retrieve Guild object for ID {guild_id} thrice')
          raise
        else:
          await asyncio.sleep(3)
    await self.load_guild_queues(guild)
    try:
      self.bot.loop.create_task(self.monitor_guild(guild))
      return True
    except ValueError:
      log.error('Failed to automatically start monitoring for %s'
                ' due to lack of an LFG channel.' % guild.name)
      raise

  def __unload(self):
    if self._init_task is not None:
      self._init_task.cancel()
    for guild_id in self.monitoring:
      self.monitoring[guild_id] = False
      self.bot.loop.create_task(
          self.clear_all_roles(self.bot.get_guild(guild_id)))

  async def cog_before_invoke(self, ctx):
    async with ctx.typing():
      await self._ready.wait()
    if self._ready_raised and time.time() - self._ready_raised > 10:
      # Immediately update timestamp to prevent multiple calls during
      # the re-attempt
      self._ready_raised = time.time()
      init_success = await self.initialize()
      if init_success:
        self._ready_raised = False
      else:
        log.info('Initialization is still incomplete.')
        self._ready_raised = time.time()
    if self._ready_raised and time.time() - self._ready_raised < 10:
      # Catches both recent failures and failures on cooldown
      await ctx.send(
          "Something's not quite right. Please wait %d seconds and try again." % (
              int(time.time() - self._ready_raised) + 3,))
      raise commands.CheckFailure()

  ####### Internal accessors

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

  async def ping(self, person, *args, **kwargs):
    if await self.config.member(person).alert():
      return await person.send(*args, **kwargs)

  async def clear_role(self, queue):
    for member in queue.role.members:
      await member.remove_roles(queue.role)

  async def clear_all_roles(self, guild: discord.Guild):
    for queue in self.guild_queues[guild.id].values():
      await self.clear_role(queue)

  async def say_to_guild(self, ctx: commands.Context, *args, **kwargs):
    if ctx.guild is not None:
      channel_id = await self.config.guild(ctx.guild).lfg_channel()
      if channel_id is not None:
        return await ctx.guild.get_channel(channel_id).send(*args, **kwargs)
    return await ctx.send(*args, **kwargs)

  async def load_guild_queues(self, guild: discord.Guild):
    guild_queues = {}
    async with self.config.guild(guild).queues() as queues:
      for queue_name, queue_config in queues.items():
        if queue_config is None:  # queue may have existed before but was deleted
          continue
        guild_queues[queue_name] = GuildQueue(
            name=queue_config['name'],
            role=discord.utils.get(guild.roles, id=queue_config['role_id']),
            default_time=queue_config['default_time'])
    self.guild_queues[guild.id].update(guild_queues)
    return guild_queues

  async def monitor_guild(self, guild: discord.Guild):
    channel_id = await self.config.guild(guild).lfg_channel()
    if channel_id is None:
      raise ValueError(
        f'Cannot monitor [{guild.name}]; it doesn\'t have a LFG output channel set.')
    self.monitoring[guild.id] = True
    while self.monitoring[guild.id]:
      for queue in self.guild_queues[guild.id].values():
        ## TODO :: Refactor duplication of say_to_guild logic here; it's being
        ## used this way for now because there's no Context object around.
        while queue.Overdue():
          member = await self.pop_from_queue(queue)
          await self.ping(
              member, "You've dropped out of the queue for %s due to timeout." % queue.dname)
          await guild.get_channel(channel_id).send(
              "%s has stopped waiting in the `%s` queue due to timeout." % (
                  member.mention, queue.name))
      await asyncio.sleep(self.watch_interval)

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
    guild_queues = await self.load_guild_queues(ctx.guild)
    await ctx.send('Loaded %d queue configurations: %s' % (
        len(guild_queues), ', '.join('`%s`' % queue_name for queue_name in guild_queues)))
    await self.queue_start.callback(self, ctx, verbose=True)

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

  @_queue.command(name='sethome')
  @commands.guild_only()
  @checks.admin()
  async def queue_set_home(self, ctx: commands.Context, channel: discord.TextChannel=None):
    """Designate the target for LFG automated messages."""
    channel = channel or ctx.channel
    await self.config.guild(ctx.guild).lfg_channel.set(channel.id)
    await ctx.send("Okay; from now on I'll send general LFG output to %s." % channel.mention)

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
  async def queue_start(self, ctx: commands.Context, verbose=True):  ## !queue start
    """Start the background process for queue monitoring in the current guild."""
    if verbose:
      await ctx.send('Starting queue monitoring.')
    try:
      await self.monitor_guild(ctx.guild)
    except ValueError:
      await ctx.send("Cannot monitor a guild that doesn't have a LFG output channel set."
                     " Use `!queue sethome <channel>` to set an output channel.")
    finally:
      self.monitoring[ctx.guild.id] = False

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
    """Join an LFG queue.

Adds you to an LFG queue, including a mentionable role, for the indicated \
number of minutes. If you do not specify the number of minutes, it is \
whatever default value is configured for the queue (probably 60 minutes).

For a list of queues, try `!lfg list`.

To remove yourself from queuing, you can `!play <opponent>`, `!play <queue_name>`, \
or simply `!lfg clear`."""
    queue = self.guild_queues[ctx.guild.id].get(queue_name.lower(), None)
    if queue is None:
      await ctx.send('Sorry, there doesn\'t appear to be an LFG queue for that.')
    else:
      ## AddMember side-effects to enqueue ctx.author. The if statement is to handle
      ## the behavior afterward depending on whether
      if await self.add_to_queue(queue, ctx.author, minutes):
        await self.say_to_guild(
            ctx, '%s has joined the %s queue (%s waiting)' % (
                ctx.author.mention, queue.role.mention,
                PersonNL(len(queue), verb=False)))
        for member in queue.ListMembers():
          if member != ctx.author:
            await self.ping(member,
                            '%s has joined you in the queue for %s.' % (
                              ctx.author.mention, queue.dname))
      else:
        await ctx.send('Okay, updating your time in the `%s` queue to %d minutes.' % (
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
  async def lfg_list(self, ctx: commands.Context, queue_name=None):
    """List all or one of the queues in this server."""
    if queue_name is not None:
      queue = self.guild_queues[ctx.guild.id].get(queue_name.lower(), None)
      if queue is None:
        return await ctx.send('Sorry, there doesn\'t appear to be an LFG queue for that.')
      if not queue:
        return await ctx.send('No one\'s currently waiting in the `%s` queue.' % queue.name)
      return await ctx.send(
          'There %s waiting in the `%s` queue:\n%s' % (
              PersonNL(len(queue), verb=True), queue.name,
              '; '.join(member.display_name for member in queue.ListMembers())))
    else:
      all_members, outputs = set(), []
      for q_name, queue in self.guild_queues[ctx.guild.id].items():
        if not queue:
          outputs.append('`%s` (0 people)' % q_name)
        else:
          all_members.update(queue.ListMembers())
          outputs.append('`%s` (%s): %s' % (
              q_name,
              PersonNL(len(queue), verb=False),
              '; '.join(member.display_name for member in queue.ListMembers())))
      return await ctx.send('There %s waiting in %d queue%s:\n%s' % (
          PersonNL(len(all_members), verb=True),
          len(self.guild_queues[ctx.guild.id]),
          's' if len(self.guild_queues[ctx.guild.id]) != 1 else '',
          '\n'.join(outputs)))

  @_lfg.command(name='alert')
  @commands.guild_only()
  async def lfg_alert(self, ctx: commands.Context):
    """Toggle DM alerts for LFG pings. (default: off)"""
    alert = await self.config.member(ctx.author).alert()
    await self.config.member(ctx.author).alert.set(not alert)
    if alert:  # Remember, this is the original value
      return await ctx.send('Okay, I won\'t send you direct messages for LFG pings.')
    return await ctx.send('Okay, I\'ll send you a direct message in addition to the'
                          ' normal LFG ping.')

  @commands.command()
  @commands.guild_only()
  async def play(self, ctx: commands.Context, *targets):
    """Play a game or an opponent, dropping out of all LFG queues.

If `target` mentions a specific player or players, you and the named \
person(s) will be dropped out of your queue, and the named person(s) will be notified.

If `target` is the name of a queue, an opponent will be chosen out of that \
queue for you at random. You may optionally append a number (e.g. `!play empyreal 3`) \
to challange that many random opponents (or up to that many, if not enough people are \
in queue."""
    if targets and targets[0].lower() in self.guild_queues[ctx.guild.id]:
      queue = self.guild_queues[ctx.guild.id][targets[0].lower()]
      try:
        if len(targets) > 2:
          raise ValueError
        num_opponents = int(targets[1]) if len(targets) == 2 else 1
        possible_opponents = queue.ListMembers()
        if ctx.author in possible_opponents:
          possible_opponents.remove(ctx.author)
        if not possible_opponents:
          return await ctx.send(
              'Sorry, there isn\'t anyone else waiting in the `%s` queue.' % queue.name)
        if num_opponents > len(possible_opponents):
          return await ctx.send(
              'There are only %d opponents available; if you\'re sure you want to play'
              ' with that many, re-run `!play %s %d`.' % (
                  len(possible_opponents), queue.name, len(possible_opponents)))
        opponents = random.sample(possible_opponents, num_opponents)
      except ValueError:
        return await ctx.send('Sorry, could not parse the rest of your request: `%s`' %
                              ' '.join(targets[1:]))
      of_game = ' of ' + queue.dname
    else:
      opponents = ctx.message.mentions
      of_game = ''

    await self.remove_from_all_queues(ctx.author, ctx.guild)
    for player in opponents:
      old_queues = await self.remove_from_all_queues(player, ctx.guild)
      await self.ping(
          player,
          '%s has challenged you to a game%s! Removing you from these queues: `%s`' % (
              ctx.author.mention, of_game, '`, `'.join(old_queues)))
    await self.say_to_guild(
        ctx, '%s -- %s has challenged you to a game%s!' % (
            ', '.join(member.mention for member in opponents), ctx.author.mention, of_game))
