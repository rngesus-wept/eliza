"""Cog for tracking players looking to play certain games."""

import asyncio
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

class Lfg:

  default_guild_settings = {
    'queues': {},
    'timeouts': {},  # a dictionary of (user, queue_name): exit_time pairings
  }

  default_member_settings = {
    'queues': [],    # a list of queue names
  }

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, 0x45B277A910C8D1E5, force_registration=True)
    self.config.register_guild(**self.default_guild_settings)
    self.config.register_member(**self.default_member_settings)
    self.monitoring = {}
    self.watch_interval = 60  # seconds

  @commands.group(name='queue', invoke_without_command=True)
  async def _queue(self, ctx: commands.Context):
    """LFG queue management functions.

    To actually join or leave queues, see the `lfg` command group."""
    await ctx.send_help()

  async def get_queue_data(self, guild, queue_name):
    return await self.config.guild(guild).get_raw(
        'queues', queue_name.lower(), default=None)

  async def set_queue_data(self, guild, queue_name, datum):
    await self.config.guild(guild).set_raw(
        'queues', queue_name.lower(), value=datum)

  @_queue.command(name='create')
  @commands.guild_only()
  @checks.admin()
  async def queue_create(self, ctx: commands.Context, name):  ## !queue create
    """Create a new queue."""
    queue_data = await self.get_queue_data(ctx.guild, name)
    if queue_data is not None:
      await ctx.send('Sorry, it looks like there\'s already a queue in place for %s.' % name)
    else:
      new_role = await ctx.guild.create_role(name='LFG %s' % name)
      config_datum = {
        'name': name,
        'role_id': new_role.id,
        'mention': new_role.mention,
        'default_time': 60,  # minutes = 1 hour
      }
      await self.set_queue_data(ctx.guild, name, config_datum)
      await new_role.edit(mentionable=True, position=1)

      await ctx.send('Created new queue with role %s' % new_role.mention)

  @_queue.command(name='list')
  @commands.guild_only()
  async def queue_list(self, ctx: commands.Context):  ## !queue list
    """List available LFG queues."""
    all_queues = await self.config.guild(ctx.guild).get_raw('queues', default=None)
    if all_queues:
      queue_list = [name for name in all_queues if all_queues[name]]
    else:
      queue_list = []
    if not queue_list:
      await ctx.send('There don\'t appear to be any LFG queues you can join...')
    else:
      await ctx.send('There %s %d queue%s you can join:\n    `%s`' % (
        'is' if len(queue_list) == 1 else 'are', len(queue_list),
        's' if len(queue_list) > 1 else '', '`, `'.join(queue_list)))

  @_queue.command(name='delete')
  @commands.guild_only()
  @checks.admin()
  async def queue_delete(self, ctx: commands.Context, name):  ## !queue delete
    """Remove a queue."""
    queue_data = await self.get_queue_data(ctx.guild, name)
    if queue_data is None:
      await ctx.send('Sorry, there doesn\'t appear to be a queue by that name.')
    else:
      role = discord.utils.get(ctx.guild.roles, id=queue_data['role_id'])
      if role is not None:
        await role.delete()
      await self.set_queue_data(ctx.guild, name, None)
      await ctx.send('OK, removed the queue for %s and its role.' % name)

  @_queue.command(name='start')
  @commands.guild_only()
  @checks.admin()
  async def queue_start(self, ctx: commands.Context):  ## !queue start
    """Start the background process for queue monitoring in the current guild."""
    await ctx.send('Okay, starting queue monitoring.')
    self.monitoring[ctx.guild.id] = True
    while self.monitoring[ctx.guild.id]:
      await ctx.send('Heartbeat')
      await asyncio.sleep(self.watch_interval)

  @_queue.command(name='stop')
  @commands.guild_only()
  @checks.admin()
  async def queue_stop(self, ctx: commands.Context):  ## !queue stop
    """Stop the background process for queue monitoring in the current guild."""
    await ctx.send('Okay, stopping queue monitoring for this guild.')
    self.monitoring[ctx.guild.id] = False

  def __unload(self):
    for guild_id in self.monitoring:
      self.monitoring[guild_id] = False

  async def add_to_queue(self, person, guild, queue, minutes):
    """Add a PERSON to QUEUE in GUILD for MINUTES.

    Returns True if that person was new to the queue, or False if their time is
    just being refreshed."""
    new_in_queue, queue_name = True, queue['name'].lower()
    queue_role = discord.utils.get(guild.roles, id=queue['role_id'])
    async with self.config.member(person).queues() as queues:
      if queue_name not in queues:
        queues.append(queue_name)
    async with self.config.guild(guild).timeouts() as timeouts:
      if (person.id, queue_name) in timeouts:
        new_in_queue = False
      timeouts[repr((person.id, queue_name))] = minutes
    await person.add_roles(queue_role)
    return new_in_queue

  async def remove_from_queue(self, person, guild, queue):
    """Remove a PERSON from QUEUE in GUILD."""
    queue_name = queue['name'].lower()
    queue_role = discord.utils.get(guild.roles, id=queue['role_id'])
    async with self.config.member(person).queues() as queues:
      queues.remove(queue_name)
    async with self.config.guild(guild).timeouts() as timeouts:
      del timeouts[repr((person.id, queue_name))]
    await person.remove_roles(queue_role)

  async def remove_from_all_queues(self, person, guild):
    for queue_name in (await self.config.member(person).queues())[:]:
      await self.remove_from_queue(person, guild,
                                   await self.get_queue_data(guild, queue_name))

  @commands.group(name='lfg', invoke_without_command=True)
  @commands.guild_only()
  async def _lfg(self, ctx: commands.Context, queue_name, minutes=0):  ## !lfg
    """Join an LFG queue."""
    queue_data = await self.get_queue_data(ctx.guild, queue_name)
    if queue_data is None:
      await ctx.send('Sorry, there doesn\'t appear to be an LFG queue for that.')
    else:
      minutes = minutes or queue_data['default_time']
      queue_role = discord.utils.get(ctx.guild.roles, id=queue_data['role_id'])
      new_lfg = await self.add_to_queue(ctx.author, ctx.guild, queue_data, minutes)
      if new_lfg:
        await ctx.send('Okay, adding you to the %s queue for %d minutes.' % (
            queue_data['name'], minutes))
        async with ctx.typing():
          time.sleep(3)
          await ctx.send('%s has joined the %s queue (%d %s waiting)' % (
              ctx.author.mention, queue_role.mention, len(queue_role.members),
              'person' if len(queue_role.members) == 1 else 'people'))
      else:
        await ctx.send('Okay, updating your time in the %s queue to %d minutes.' % (
            queue_data['name'], minutes))

  @_lfg.command(name='clear')
  @commands.guild_only()
  async def lfg_clear(self, ctx: commands.Context):
    """Remove yourself from all LFG queues in this server."""
    await ctx.send('Okay, removing you from all queues in this server.')
    await self.remove_from_all_queues(ctx.author, ctx.guild)
