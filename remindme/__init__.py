from redbot.core.bot import Red
from .remindme import RemindMe


def setup(bot: Red):
  module = RemindMe(bot)
  bot.add_cog(module)
