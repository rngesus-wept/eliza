"""Cog for managing tagged FAQs within a channel."""

import functools
import time

import discord
from discord.ext import commands

from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red
from redbot.core.utils.menu import menu, DEFAULT_CONTROLS


## There are basically three options for data storage here:

## 1. Use the built-in config library, and _only_ that. This basically means
##    that all manipulations must be done in JSON-dictable data formats. Thus
##    the main drawback is the huge hit to readability, since additional words
##    must be expended in service of the config API. In addition, it is not
##    clear how well manipulation of config (i.e. of a backing JSON/MongoDB
##    file on disk) scales upward.

## 2. Use the built-in config library, as 1., but use intermediary objects to
##    hold most of the data in memory. This means that we can massage the
##    syntax into something readable. The biggest drawback here is ensuring
##    that the data in memory is synced with the backing config; we need to be
##    careful that any write operations are mirrored upward. This can be
##    managed with careful crafting of the API and wrappers, but is still worth
##    mentioning. As a secondary drawback, it's a lot easier to run out of
##    memory than it is to run out of disk. (Though perhaps for a VPS/cloud
##    system, these things are equivalent.)

## 3. Use a real backing database; possibly Redis. There's a massive
##    infrastructure activation bump here, as we'd have to not only figure out
##    the system-level maintenance configs, but also write data access
##    management functions. Worth mentioning as an option, but at that point we
##    should really consider writing a Redis driver for Config _first_ and then
##    working through that. The biggest advantage this has is that we can then
##    do database-style (i.e. SQL-style) queries on the data, which is
##    efficient and readable in ways that would be hard to replicate (but
##    again, the former is perhaps not relevant at our current scale).

class Faqlet:

  def __init__(self, id, config, question, answer, creator, created=None,
               last_editor=None, last_edited=None, tags=None):
    self.id = id
    self.question = question
    self.answer = answer
    self.creator = creator
    self.created = created or time.time()
    self.last_editor = last_editor
    self.last_edited = last_edited
    self.tags = tags or []

  def sync(self):
    """Decorator ensuring that the wrapped function will sync this object to config."""
    @functools.wraps(func)
    async def wrapped_fn(*args, **kwargs):
      return await func(*args, **kwargs)
    return wrapped

  def edit_impl(self):
    @functools.wraps(func)
    async def wrapped_dummy(**kwargs):
      for attr, value in kwargs.items():
        self.last_edited = time.time()  # Can be overwritten by manually passed value
        if attr == 'tags':
          for tag in value:
            if tag[0] == '-':
              self.tags.remove(tag[1:])
            elif tag[0] == '+':
              self.tags.append(tag[1:])
            else:
              self.tags.append(tag)
        elif attr == 'editor':
          self.last_editor = None if value == self.creator else value
        else:
          setattr(self, attr, value)
      return await func(**kwargs)
    return wrapped_dummy

  @self.sync
  @self.edit_impl
  async def edit(self, **kwargs):
    pass


class Faq:
  """Red cog for managing FAQs."""

  ## As tagged FAQs get added to the FAQ, the guild config will take on new
  ## pairs mapping each tag to a list of FAQs with that tag, for easy reverse lookup.
  ## '_deleted' is a special tag for the IDs of formally deleted FAQs.
  default_guild_settings = {
      '_next_faq_id': 0,
      '_faqs': [],
      '_deleted': [],
  }

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, 0x92A804678C03D64D, force_registration=True)
    self.config.register_guild(**self.default_guild_settings)

  @commands.group(name='faq')
  async def _faq(self, ctx: commands.Context):
    """Frequently asked questions database."""
    if ctx.invoked_subcommand is None:
      await ctx.send_help()

  @_faq.command(name='new')
  @commands.guild_only()
  @checks.mod()
  async def faq_new(self, ctx: commands.Context, question):
    """Create a new FAQ entry.

    Simply input the question, e.g. `!faq How do clashes work?`. The bot will PM
    you for the response to the question, with a 5 minute timeout."""
    pass
