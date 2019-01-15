"""Cog for issuing timed reminders.

Based on Twentysix's implementation for Red v2."""

import asyncio
import logging
import time

import discord
from redbot.core import commands

from redbot.core import Config
from redbot.core.bot import Red


log = logging.getLogger("remindme")
log.setLevel(logging.INFO)


class RemindMe(commands.Cog):
  """Cog for issuing timed reminders."""

  default_user_settings = {
      'reminders': {},
  }

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, 0xEDFB993DF88A894D, force_registration=True)
    self.config.register_user(**self.default_user_settings)
    self.monitoring_interval = 10  # seconds
    self.reminder_task = self.bot.loop.create_task(self.CheckReminders())
    self.units = {
        'second': 1,
        'minute': 60,
        'hour': 3600,
        'day': 24 * 60 * 60,
        'week': 7 * 24 * 60 * 60,
        'month': 30 * 24 * 60 * 60,
        'year': 365 * 24 * 60 * 60
    }

  def __unload(self):
    self.reminder_task.cancel()

  async def _AddReminder(self, user: discord.User, duration_s: float, message: str):
    """Save USER's MESSAGE for retransmission in DURATION_S seconds."""
    log.info('Recording reminder %r for %s in %f seconds.' % (
        message, user.name, duration_s))
    async with self.config.user(user).reminders() as reminders:
      reminders[str(time.time() + duration_s)] = message

  async def _ShortReminder(self, user: discord.User, duration_s: float, message: str):
    """Remind USER of MESSAGE in DURATION_S seconds."""
    ## This method is used for short-term manipulation of reminders, i.e.
    ## within 2x the bot's resolution for checking the reminder list. This
    ## allows us to reduce the number of individual reads from config while
    ## maintaining second-level resolution of reminders.
    await asyncio.sleep(duration_s)
    await user.send('Reminder: `%s`' % message)

  async def CheckReminders(self):
    """Monitoring loop for reminders."""
    while not self.bot.is_closed():
      data = await self.config.all_users()
      for user_id in data:
        user = self.bot.get_user(user_id)
        async with self.config.user(user).reminders() as reminders:
          to_remove = []
          for timestamp, message in reminders.items():
            now = time.time()
            if now >= float(timestamp):
              await user.send('Reminder: `%s`' % message)
              to_remove.append(timestamp)
            elif float(timestamp) - now <= 2 * self.monitoring_interval:
              self.bot.loop.create_task(
                  self._ShortReminder(user, float(timestamp) - now, message))
              to_remove.append(timestamp)
          for timestamp in to_remove:
            del reminders[timestamp]
      await asyncio.sleep(self.monitoring_interval)

  @commands.command(name='remindme')
  async def CreateReminder(self, ctx: commands.Context,
                           quantity: int, time_unit: str, *, text: str):
    """Reminds you of `text` after `quantity` `time_unit`.

Acceptable time units are: second(s), minute(s), hour(s), day(s), week(s), month(s) \
(30 days), year(s) (365 days)"""

    ## Input validation
    time_unit, plural = time_unit.lower(), ''
    if time_unit.endswith('s'):
      time_unit, plural = time_unit[:-1], 's'
    if time_unit not in self.units:
      return await ctx.send(
          "I don't think `%s` is a time unit. Try one of `%s` instead." % (
              time_unit, '`/`'.join(self.units)))
    if quantity < 1:
      return await ctx.send("Sorry, time travel is hard.")
    if len(text) > 1960:
      return await ctx.send("That's too much text for a reminder.")

    ## Actually do things
    duration = self.units[time_unit] * quantity
    if duration <= 2 * self.monitoring_interval:
      self.bot.loop.create_task(self._ShortReminder(ctx.author, duration, text))
      await ctx.send("Will do!")
    else:
      await self._AddReminder(ctx.author, duration, text)
      await ctx.send("Okay, I'll remind you of that in %s %s%s." % (
          quantity, time_unit, plural))

  @commands.command(name='forgetme')
  async def ClearReminders(self, ctx: commands.Context):
    """Clears all your upcoming long-term reminders.

Reminders scheduled to occur within the next minute or so may not be cleared."""
    await self.config.user(ctx.author).set_raw('reminders', value={})
    await ctx.send("Okay, I've removed all your upcoming reminders, except for those"
                   " set to go off in %d seconds." % (2 * self.monitoring_interval))
