from redbot.core.bot import Red
from .faq import Faq


def setup(bot: Red):
  faq_module = Faq(bot)
  bot.add_cog(faq_module)
