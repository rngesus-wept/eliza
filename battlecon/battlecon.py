from redbot.core import commands

import discord
from .session import BCSession
from redbot.core import Config
from redbot.core.utils.chat_formatting import box, pagify


UNIQUE_ID = 0x92AB77135B04AD06


class Battlecon(commands.Cog):
  """Sparring for BattleCON."""

  def __init__(self):
    super().__init__()
    self.spar_sessions = []
    self.conf = Config.get_conf(self, identifier=UNIQUE_ID, force_registration=True)

  @commands.group(invoke_without_command=True)
  async def spar(self, ctx: commands.Context):
    """BattleCON sparring session commands.

    Practice your BattleCON under timed conditions! For full details, type `[p]spar rules`."""
    pass

  @spar.command(name='rules', aliases=['info'])
  async def spar_rules(self, ctx: commands.Context):
    """Sparring rules."""
    rules_text = ""
