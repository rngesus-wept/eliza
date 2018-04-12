from redbot.core.bot import Red
from .secretkeeper import SecretKeeper


def setup(bot: Red):
  bot.add_cog(SecretKeeper(bot))
