from redbot.core.bot import Red
from .lfg import GuildQueue, Lfg


def setup(bot: Red):
  lfg_module = Lfg(bot)
  bot.add_cog(lfg_module)
