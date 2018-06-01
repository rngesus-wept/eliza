"""Cog for managing tagged FAQs within a channel."""

import asyncio
from datetime import datetime
import functools

from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

import discord
from discord.ext import commands


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

# class Faqlet:

#   def __init__(self, id, config, question, answer, creator, created=None,
#                last_editor=None, last_edited=None, tags=None):
#     self.id = id
#     self.question = question
#     self.answer = answer
#     self.creator = creator
#     self.created = created or time.time()
#     self.last_editor = last_editor
#     self.last_edited = last_edited
#     self.tags = tags or []

#   def sync(self):
#     """Decorator ensuring that the wrapped function will sync this object to config."""
#     @functools.wraps(func)
#     async def wrapped_fn(*args, **kwargs):
#       return await func(*args, **kwargs)
#     return wrapped

#   def edit_impl(self):
#     @functools.wraps(func)
#     async def wrapped_dummy(**kwargs):
#       for attr, value in kwargs.items():
#         self.last_edited = time.time()  # Can be overwritten by manually passed value
#         if attr == 'tags':
#           for tag in value:
#             if tag[0] == '-':
#               self.tags.remove(tag[1:])
#             elif tag[0] == '+':
#               self.tags.append(tag[1:])
#             else:
#               self.tags.append(tag)
#         elif attr == 'editor':
#           self.last_editor = None if value == self.creator else value
#         else:
#           setattr(self, attr, value)
#       return await func(**kwargs)
#     return wrapped_dummy

#   @self.sync
#   @self.edit_impl
#   async def edit(self, **kwargs):
#     pass


class Faq:
  """Red cog for managing FAQs."""

  ## As tagged FAQs get added to the FAQ, the guild config will take on new
  ## pairs mapping each tag to a list of FAQs with that tag, for easy reverse lookup.
  ## '_deleted' is a special tag for the IDs of formally deleted FAQs.
  default_guild_settings = {
      '_faqs': [],
      '_deleted': [],
  }

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, 0x92A804678C03D64D, force_registration=False)
    self.config.register_guild(**self.default_guild_settings)

  @commands.group(name='faq')
  async def _Faq(self, ctx: commands.Context):
    """Frequently asked questions database."""
    if ctx.invoked_subcommand is None:
      await ctx.send_help()

  @_Faq.command(name='new')
  @commands.guild_only()
  @checks.mod()
  async def FaqNew(self, ctx: commands.Context, *q):
    """Create a new FAQ entry.

Simply input the question, e.g. `[p]faq new How do clashes work?`. The bot will \
PM you for the response to the question, with a 5 minute timeout. Note that \
Markdown is supported in questions and answers, e.g. \\*\\*bold\\*\\*, \
\\/italic\\/, \\_underline\\_, \\~strikethrough\\~.

Remember to search around a bit to see if your question is already in the \
database."""
    ## Syntax checking
    question = ' '.join(q)
    if not question.strip():
      await ctx.send('I can\'t accept an FAQ entry that doesn\'t have a question.')
      return
    elif ctx.message.mentions or ctx.message.mention_everyone:
      await ctx.send('Please don\'t mention Discord members in your question.')
      return

    await ctx.author.send("Tell me: `{}`".format(question))

    try:
      answer = await ctx.bot.wait_for(
          'message',
          check=lambda m: m.guild is None and m.author == ctx.author,
          timeout=300)
    except asyncio.TimeoutError:
      await ctx.author.send("Sorry, make your request again when you\'re ready.")
      return

    async with self.config.guild(ctx.guild)._faqs() as faqs:
      new_faq = {
          'id': len(faqs),
          'question': question,
          'answer': answer.content,
          'creator': ctx.author.id,
          'created': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
          'last_editor': None,
          'last_edit': None,
          'tags': []}
      faqs.append(new_faq)
    await ctx.send(
        content=("Thanks for contributing to the FAQ! Don't forget to use"
                 " `!faq %d <tag1> [<tag2> <tag3>...]` to make your entry"
                 " searchable.") % new_faq['id'],
        embed=self.FaqEmbed(ctx.guild, **new_faq))

  @_Faq.command(name='tag')
  @commands.guild_only()
  @checks.mod()
  async def FaqTag(self, ctx: commands.Context, faq_id, *tags):
    """Add a tag or tags to the indicated FAQ entry.

Tags are case-insensitive. For multi-word tags, surround the tag in quotes, \
e.g. `[p]faq 1 "end of beat"`. If an input starts with a hyphen, that tag will \
instead be removed from the FAQ entry."""
    faq_id = int(faq_id)
    if set(['_faqs', '-_faqs']) & set(tags):
      return await ctx.send('`_faqs` is not a valid tag.')
    elif set(['_deleted', '-_deleted']) & set(tags):
      return await ctx.send('`_deleted` is a reserved tag; please use'
                            ' `!faq delete` or `!faq undelete` instead.')

    async with self.config.guild(ctx.guild)._faqs() as faqs:
      faq_tags = faqs[faq_id]['tags']
      for tag in map(str.lower, tags):
        if tag[0] == '-' and tag[1:] in faq_tags:
          async with self.config.guild(ctx.guild).getattr(tag[1:])() as tag_backref:
            tag_backref.remove(faq_id)
          faq_tags.remove(tags[1:])
        elif tag not in faq_tags:
          tag_backref = await self.config.guild(ctx.guild).get_raw(tag, default=None) or []
          tag_backref.append(faq_id)
          await self.config.guild(ctx.guild).set_raw(tag, value=tag_backref)
          faq_tags.append(tag)
    await ctx.send('Got it. The tags for FAQ entry %d are now `%r`.' % (
        faq_id, (await self.config.guild(ctx.guild)._faqs())[faq_id]['tags']))

  @_Faq.command(name='show')
  @commands.guild_only()
  async def FaqShow(self, ctx: commands.Context, faq_id):
    faq_entry = (await self.config.guild(ctx.guild)._faqs())[int(faq_id)]
    await ctx.send(embed=self.FaqEmbed(ctx.guild, **faq_entry))

  def FaqEmbed(self, guild, *, id, question, answer, creator, created,
               last_editor, last_edit, tags):
    embed = discord.Embed(title=question,
                          color=discord.Color(0xff0000 if '_deleted' in tags else 0x22aaff),
                          description=answer,
                          timestamp=datetime.strptime(created, '%Y-%m-%dT%H:%M:%S.%fZ'))
    author = guild.get_member(creator).display_name
    icon = guild.get_member(creator).avatar_url
    if last_editor:
      author += ' (last edited by %s)' % guild.get_member(last_editor).display_name
      icon = guild.get_member(last_editor).avatar_url
    embed.set_author(name=author, icon_url=icon)
    embed.set_footer(text=', '.join(tags))
    return embed


  @_Faq.command(name='test')
  async def FaqTest(self, ctx: commands.Context, x):
    embed = discord.Embed(title='x', description=x)
    embed.set_author(name=ctx.guild.get_member(ctx.author.id).display_name)
    await ctx.send(content='test', embed=embed)
