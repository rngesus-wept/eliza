from redbot.core.bot import Red
from .mindgames import MindGames


def setup(bot: Red):
  bot.add_cog(MindGames(bot))
