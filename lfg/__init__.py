from redbot.core.bot import Red
from .lfg import GuildQueue, Lfg  # GuildQueue import for debugging access via !eval


def setup(bot: Red):
  lfg_module = Lfg(bot)
  bot.add_cog(lfg_module)
