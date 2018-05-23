from redbot.core.bot import Red
from .lfg import Lfg


def setup(bot: Red):
  bot.add_cog(Lfg(bot))
