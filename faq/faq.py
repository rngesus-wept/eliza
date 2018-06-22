"""Cog for managing tagged FAQs within a channel."""

import asyncio
from datetime import datetime
from fuzzywuzzy import process

from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red
from redbot.core.utils.menus import menu, prev_page, next_page

import discord
from discord.ext import commands


## TODO: Q/A refactoring

async def close_menu(ctx: commands.Context, pages: list, controls: dict,
                     message: discord.Message, page: int, timeout: float, emoji: str):
  ## This overrides the normal "close" behavior in redbot.core.utils.menus in
  ## that it clears reactions instead of deleting the search results.
  try:
    await message.clear_reactions()
  except discord.Forbidden:
    for key in controls.keys():
      await message.remove_reaction(key, ctx.bot.user)
  return None

DEFAULT_CONTROLS = {"⬅": prev_page, "❌": close_menu, "➡": next_page}

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

  @commands.group(name='faq', autohelp=False)
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
take your next message in the same channel as the response to the question, with \
a 5 minute timeout. Note that Markdown is supported in questions and answers, \
e.g. \\*\\*bold\\*\\*, \\/italic\\/, \\_underline\\_, \\~strikethrough\\~.

Remember to search around a bit to see if your question is already in the \
database."""
    ## Syntax checking
    question = ' '.join(q)
    if not question.strip():
      return await ctx.send('I can\'t accept an FAQ entry that doesn\'t have a question.')
    elif ctx.message.mentions or ctx.message.mention_everyone:
      return await ctx.send('Please don\'t mention Discord members in your question.')

    await ctx.send(
        "Okay {}, waiting on your response to ```{}``` (or `!cancel`)".format(
            ctx.author.mention, question))

    try:
      answer = await ctx.bot.wait_for(
          'message',
          check=lambda m: m.channel == ctx.channel and m.author == ctx.author,
          timeout=300)
    except asyncio.TimeoutError:
      return await ctx.send("Sorry, cancelling FAQ creation due to timeout."
                            " Make your request again when you\'re ready.")

    if answer.mentions or answer.mention_everyone:
      return await ctx.send('Please don\'t mention Discord members in your response.')
    elif answer.content.lower() == '!cancel':
      return await ctx.send('Okay %s, cancelling FAQ creation.' % ctx.author.mention)

    async with self.config.guild(ctx.guild)._faqs() as faqs:
      new_faq = {
          'id': len(faqs),
          'question': question,
          'answer': answer.content,
          'creator': ctx.author.id,
          'created': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
          'last_editor': None,  # an ID
          'last_edit': None,
          'tags': []}
      faqs.append(new_faq)
    await ctx.send(
        content=("Thanks for contributing to the FAQ, %s! Don't forget to use"
                 " `!faq tag %d <tag1> [<tag2> <tag3>...]` to make your entry"
                 " searchable.") % (ctx.author.mention, new_faq['id']),
        embed=self.FaqEmbed(ctx.guild, **new_faq))

  async def GetFaqEntry(self, ctx: commands.Context, faq_id, verbose=True):
    """Get the FAQ dictionary item corresponding to faq_id."""
    try:
      return (await self.config.guild(ctx.guild)._faqs())[int(faq_id)]
    except ValueError:
      if verbose:
        await ctx.send("`%s` is not a valid FAQ ID." % faq_id)
    except IndexError:
      if verbose:
        await ctx.send("There's no FAQ entry with ID %s." % faq_id)
    return None

  @_Faq.command(name='edit-q')
  @commands.guild_only()
  @checks.mod()
  async def FaqEditQuestion(self, ctx: commands.Context, faq_id: int):
    """Edit the question for a FAQ entry."""
    faq_entry = await self.GetFaqEntry(ctx, faq_id, verbose=True)
    if faq_entry is None:
      return

    await ctx.send(
        "Okay %s, waiting on your change to the question for FAQ %d (or `!cancel`)."
        " Here's the raw value, for your convenience: ```%s```" % (
            ctx.author.mention, faq_id, faq_entry['question']))

    try:
      question = await ctx.bot.wait_for(
          'message',
          check=lambda m: m.channel == ctx.channel and m.author == ctx.author,
          timeout=300)
    except asyncio.TimeoutError:
      return await ctx.send("Sorry, cancelling the question edit due to timeout. "
                            "Make your request again when you're ready.")

    if question.mentions or question.mention_everyone:
      return await ctx.send("Please don't mention Discord members in your question.")
    elif question.content.lower() == '!cancel':
      return await ctx.send("Okay %s, cancelling edit on FAQ %d." % (ctx.author.mention, faq_id))

    async with self.config.guild(ctx.guild)._faqs() as faqs:
      faqs[faq_id]['question'] = question.content
      faqs[faq_id]['last_editor'] = (None if faq_entry['creator'] == ctx.author.id
                                     else ctx.author.id)
      faqs[faq_id]['last_edit'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
      faq_entry = dict(faqs[faq_id])
      await ctx.send(
          content="Okay %s, made your edit to FAQ %d:" % (ctx.author.mention, faq_id),
          embed=self.FaqEmbed(ctx.guild, **faq_entry))

  @_Faq.command(name='edit-a')
  @commands.guild_only()
  @checks.mod()
  async def FaqEditAnswer(self, ctx: commands.Context, faq_id: int):
    """Edit the answer for a FAQ entry."""
    faq_entry = await self.GetFaqEntry(ctx, faq_id, verbose=True)
    if faq_entry is None:
      return

    await ctx.send(
        "Okay %s, waiting on your change to the answer for FAQ %d (or `!cancel`)."
        " Here's the raw value, for your convenience: ```%s```" % (
            ctx.author.mention, faq_id, faq_entry['answer']))

    try:
      answer = await ctx.bot.wait_for(
          'message',
          check=lambda m: m.channel == ctx.channel and m.author == ctx.author,
          timeout=300)
    except asyncio.TimeoutError:
      return await ctx.send("Sorry, cancelling the answer edit due to timeout. "
                            "Make your request again when you're ready.")

    if answer.mentions or answer.mention_everyone:
      return await ctx.send("Please don't mention Discord members in your answer.")
    elif answer.content.lower() == '!cancel':
      return await ctx.send("Okay %s, cancelling edit on FAQ %d." % (ctx.author.mention, faq_id))

    async with self.config.guild(ctx.guild)._faqs() as faqs:
      faqs[faq_id]['answer'] = answer.content
      faqs[faq_id]['last_editor'] = (None if faq_entry['creator'] == ctx.author.id
                                     else ctx.author.id)
      faqs[faq_id]['last_edit'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
      faq_entry = dict(faqs[faq_id])
      await ctx.send(
          content="Okay %s, made your edit to FAQ %d:" % (ctx.author.mention, faq_id),
          embed=self.FaqEmbed(ctx.guild, **faq_entry))

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
      faq_tags = set(faqs[faq_id]['tags'])
      to_add = {tag.lower() for tag in tags if tag[0] != '-'}
      to_rem = {tag[1:].lower() for tag in tags if tag[0] == '-'}
      faq_tags = (faq_tags | to_add) - to_rem

      for tag in to_add | to_rem:
        tag_backref = await self.config.guild(ctx.guild).get_raw(tag, default=None) or []
        if tag in to_add and faq_id not in tag_backref:
          tag_backref.append(faq_id)
        elif tag in to_rem and faq_id in tag_backref:
          tag_backref.remove(faq_id)
        await self.config.guild(ctx.guild).set_raw(tag, value=tag_backref)

      faqs[faq_id]['tags'] = list(faq_tags)

    await ctx.send('Got it. The tags for FAQ entry %d are now `%r`.' % (
        faq_id, (await self.config.guild(ctx.guild)._faqs())[faq_id]['tags']))

  @_Faq.command(name='show')
  @commands.guild_only()
  async def FaqShow(self, ctx: commands.Context, faq_id):
    """Show the FAQ entry with the given ID."""
    faq_entry = await self.GetFaqEntry(ctx, faq_id, verbose=True)
    if faq_entry is not None:
      await ctx.send(embed=self.FaqEmbed(ctx.guild, **faq_entry))

  @_Faq.command(name='search')
  @commands.guild_only()
  async def FaqSearch(self, ctx: commands.Context, *tags):
    """Search for FAQ entries that have all the listed tags.

Separate tags with spaces. For multi-word tags, use quotes. Deleted FAQ entries will \
not show up unless you specify `_deleted` as a tag. If any tags listed do not exist, \
the search will instead suggest close matches for the missed tags."""
    tags = list(map(str.lower, tags))
    get_deleted = '_deleted' in tags
    if get_deleted:
      tags.remove('_deleted')
    all_tags = list(await self.config.guild(ctx.guild).all())
    all_tags.remove('_faqs')
    missing_tags = set(tags) - set(all_tags)
    if missing_tags:
      suggestions = []
      for tag in missing_tags:
        suggest = []
        for fuzzed_tag, rating in process.extract(tag, all_tags, limit=5):
          if rating < 50:
            break
          suggest.append('"%s"' % fuzzed_tag if ' ' in fuzzed_tag else fuzzed_tag)
        suggestions.append("`%s` -- Instead try `%s`" % (tag, "`, `".join(suggest)))

      return await ctx.send(
          "I couldn't find any matches for the following tags:\n" + "\n".join(suggestions))

    hits = set(await self.config.guild(ctx.guild).get_raw(tags[0]))
    for tag in tags[1:]:
      hits &= set(await self.config.guild(ctx.guild).get_raw(tag))
    if not hits:
      return await ctx.send(
          "I couldn't find any entries matching that tag or combination of tags.")
    else:
      faq_entries = []
      for faq_id in hits:
        data = (await self.config.guild(ctx.guild)._faqs())[int(faq_id)]
        if get_deleted or '_deleted' not in data['tags']:
          ## Only include _deleted hits if it was explicitly requested
          faq_entries.append(self.FaqEmbed(ctx.guild, **data))
      if not faq_entries:
        return await ctx.send(
            "I couldn't find any entries matching that tag or combination of tags.")
      elif len(faq_entries) == 1:
        return await ctx.send(embed=faq_entries[0])
      else:
        await menu(ctx, faq_entries, DEFAULT_CONTROLS, timeout=120)

  def FaqEmbed(self, guild, *, id, question, answer, creator, created,
               last_editor, last_edit, tags):
    embed = discord.Embed(title='(#%d) %s' % (id, question),
                          color=discord.Color(0xff0000 if '_deleted' in tags else 0x22aaff),
                          description=answer,
                          timestamp=datetime.strptime(last_edit or created,
                                                      '%Y-%m-%dT%H:%M:%S.%fZ'))
    author = guild.get_member(creator).display_name
    icon = guild.get_member(creator).avatar_url
    if last_editor:
      author += ' (last edited by %s)' % guild.get_member(last_editor).display_name
      icon = guild.get_member(last_editor).avatar_url
    embed.set_author(name=author, icon_url=icon)
    embed.set_footer(text=', '.join(tags))
    return embed
