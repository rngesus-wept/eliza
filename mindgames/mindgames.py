import logging
import random

from discord.ext import commands
from redbot.core import checks
from redbot.core import Config

from redbot.core.bot import Red


logger = logging.getLogger("red.mindgames")


class MindGames:

  def __init__(self, bot: Red):
    self.config = Config.get_conf(self, identifier=0x452AB490D23D)
    self.config.register_member(test_coin=1000)

  @commands.group(name="mg")
  async def _mg(self, ctx: commands.Context):
    """Mindgame operations"""
    if ctx.invoked_subcommand is None:
      await ctx.send_help()

  @_mg.command()
  async def randomgain(self, ctx: commands.Context):
    balance = await self.config.member(ctx.author).test_coin()
    amount = random.randint(1, 100)
    await self.config.member(ctx.author).test_coin.set(balance + amount)
    await ctx.send(
      'You just gained {0} blobs! ({1} total)'.format(
        amount, balance + amount))

  @_mg.command()
  async def show(self, ctx: commands.Context):
    balance = await self.config.member(ctx.author).test_coin()
    await ctx.send('You currently have {0} blobs!'.format(balance))
