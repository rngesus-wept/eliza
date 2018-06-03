from redbot.core.bot import Red
from .lfg import GuildQueue, Lfg  # GuildQueue import for debugging access via !eval


async def setup(bot: Red):
  lfg_module = Lfg(bot)
  await lfg_module.initialize()
  bot.add_cog(lfg_module)
