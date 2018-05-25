from redbot.core.bot import Red
from .lfg import Lfg


def setup(bot: Red):
  lfg_module = Lfg(bot)
  bot.add_cog(lfg_module)
