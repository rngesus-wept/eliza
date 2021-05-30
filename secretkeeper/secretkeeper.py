"""Cog for keeping and divulging secrets of various types.

A secret has the following properties:

  * public value -- This is a publicly visible hash digest of some kind.
  * private value -- This is the actual content of the secret. If the secret
      does not have bot visibility, it instead has value None.
  * salt -- The salt which is concatenated to the secret's private value
      before hashing.
  * creation time -- A timestamp (int) in epoch seconds.
  * peek-permitted roles -- Roles and identities which are allowed to view
      the private value of the secret directly.
  * reveal-permitted roles -- Roles and identities which are allowed to
      reveal the private value of the secret directly.
"""

import asyncio
import hashlib
import logging
import random
import time

import discord
from redbot.core import commands

from redbot.core import Config
from redbot.core.bot import Red

from . import utils

log = logging.getLogger('red.eliza.secretkeeper')


class SecretKeeper:
  """A secret-keeping cog with lots of scoping and visibility options.

Go on, tell it where the bodies are buried!"""

  default_guild_settings = {
    'hash_type': 'sha256',
    'salt': {
      'chars': ('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef'
                'ghijklmnopqrstuvwxyz0123456789/*'),
      'length': 20,
    },
    'secrets': {},
  }

  default_member_settings = {
    'secrets': [],
  }

  def __init__(self, bot: Red):
    self.bot = bot
    self.config = Config.get_conf(self, 0x95B748236AB8978B, force_registration=True)
    self.config.register_guild(**self.default_guild_settings)
    self.config.register_member(**self.default_member_settings)

  async def make_digest(self, guild, content):
    hash_type = await self.config.guild(guild).hash_type()
    salt_cfg = self.config.guild(guild).salt
    salt_chars = await salt_cfg.chars()
    salt_length = await salt_cfg.length()

    salt = rng_salt(salt_chars, salt_length)
    return digest(hash_type, content, salt), salt

  @commands.group(name='secret', autohelp=False)
  async def _secret(self, ctx: commands.Context):
    """Secrets-manipulating operations.

    A **secret** is phrase hidden by hashing, so that its contents can be revealed
    against a later public reveal. See subcommand docs for details, starting with
    `new`, `peek`, and `reveal`."""
    if ctx.invoked_subcommand is None:
      await ctx.send_help()

  @_secret.command(name='new')
  @commands.guild_only()
  async def secret_new(self, ctx: commands.Context, *desc):
    """Create a new secret with the given description.

    The bot will request the private contents of the secret from you via PM, with
    a five-minute timeout.

    Example: `!secret new Test Secret Please Ignore`"""
    desc = ' '.join(desc)
    if not desc.strip():  # no description provided
      await ctx.send('I need some kind of description for what\'s in this secret.'
                     ' (You\'ll be glad for it later too, I suspect.)')
      return
    await ctx.author.send(("I'm listening for your secret about `{}`; simply"
                           " reply to this message.").format(desc))
    try:
      content = await ctx.bot.wait_for(
          'message',
          check=lambda m: m.guild is None and m.author == ctx.author,
          timeout=300)
    except asyncio.TimeoutError:
      await ctx.author.send("Sorry, make your request again when you're ready.")
    else:
      digest, salt = await self.make_digest(ctx.guild, content.content)
      created = time.time()

      guild_secrets = self.config.guild(ctx.guild).secrets
      secrets_dict = await guild_secrets.all()
      secrets_dict['_' + digest] = {
        'content': content.content,
        'salt': salt,
        'created': created,
        'creator': ctx.author.mention,
        'desc': desc,
        'peek': ['u:' + str(ctx.author.id)],
        'reveal': ['u:' + str(ctx.author.id)]
      }
      await guild_secrets.set(secrets_dict)

      await ctx.author.send(
          ':thumbsup: Stored your secret about {} as `{}...`'.format(
            desc, digest[:8]))

  async def check_permission(self, user: discord.Member, whitelist):
    keys = ['u:' + user.id] + ['r:' + role.id for role in user.roles]
    return bool(set(keys) & set(whitelist))

  async def get_all_secrets(self, guild):
    secrets_dict = await self.config.guild(guild).secrets.all()
    return {digest[1:]: secret for digest, secret in secrets_dict.items()}

  async def get_some_secrets(self, guild, digest_prefix):
    secrets_dict = await self.config.guild(guild).secrets.all()
    return {digest[1:]: secret for digest, secret in secrets_dict.items()
            if digest.startswith('_' + digest_prefix)}

  async def get_secret(self, guild, digest):
    secret = await getattr(self.config.guild(guild).secrets, '_' + digest).all()
    return secret

  @_secret.command(name='peek')
  @commands.guild_only()
  async def secret_peek(self, ctx: commands.Context, digest):
    """Peek at the contents of a secret."""
    secrets_dict = await self.get_some_secrets(ctx.guild, digest)
    for output in format_secrets_public(secrets_dict):
      await ctx.author.send(output)

  @_secret.command(name='reveal')
  async def secret_reveal(self, ctx: commands.Context, digest):
    """Publicly reveal the contents of a secret."""
    pass

  @_secret.command(name='list')
  async def secret_list(self, ctx: commands.Context, prefix=None):
    """List some or all secrets, sorting by access."""
    if prefix is None:
      secrets_dict = await self.get_all_secrets(ctx.guild)
    else:
      secrets_dict = await self.get_some_secrets(ctx.guild, prefix)

    for embed in utils.paginated_embed_fields(
        title='Secrets', color=0xDD3333,
        fields=format_secrets_list(secrets_dict, ctx.author)):
      await ctx.send(embed=embed)
      await asyncio.sleep(.5)

  @_secret.command(name='mine')
  async def secret_mine(self, ctx: commands.Context, prefix=None):
    """List secrets created by the caller."""
    if prefix is None:
      secrets_dict = await self.get_all_secrets(ctx.guild)
    else:
      secrets_dict = await self.get_some_secrets(ctx.guild, prefix)

    my_secrets_dict = {digest: secret for digest, secret in secrets_dict.items()
                       if secret['creator'] == ctx.author.mention}

    for embed in utils.paginated_embed_content(
        title='Your secrets',
        content='\n'.join(format_secret(digest, secret)
                          for digest, secret
                          in trim_digest_display(my_secrets_dict)),
        color=0x3333DD):
      await ctx.send(embed=embed)
      await asyncio.sleep(.5)

  @_secret.command(name='delete')
  async def secret_delete(self, ctx: commands.Context, digest):
    """Delete a secret."""
    secrets_dict = await self.get_some_secrets(ctx.guild, digest)

    pass

  @_secret.command(name='share')
  async def secret_share(self, ctx: commands.Context, digest, target):
    """Allow a person or role to peek/reveal the secret."""
    pass

  @_secret.command(name='config')
  async def secret_config(self, ctx: commands.Context, key, value):
    """Configure guild secret-keeping settings."""
    pass

  @_secret.command(name='password')
  async def secret_password(self, ctx: commands.Context, *desc):
    """Create a secret whose value the bot doesn't store."""
    pass


def digest(hash_fn, content, salt):
  return getattr(hashlib, hash_fn)(
      (content + salt).encode('latin-1')).hexdigest()


def rng_salt(chars, length):
  return ''.join(random.choice(chars) for _ in range(length))


def trim_digest_display(secrets_dict):
  """Trim digest keys down as much as possible, preserving uniqueness."""
  for prefix_length in range(8, 100):
    new_keys = set(digest[:prefix_length] for digest in secrets_dict)
    if len(new_keys) == len(secrets_dict):
      return sorted(((digest[:prefix_length], secret)
                     for digest, secret in secrets_dict.items()),
                    key=lambda pair: pair[0])


def format_secret(digest, secret):
  return '`{}...`: {}\n    by {} at {}'.format(
    digest, secret['desc'], secret['creator'],
    time.strftime('%Y-%m-%d %H:%M:%S UTC%z', time.localtime(secret['created'])))


def format_secrets_list(secrets_dict, user: discord.Member):
  new_dict = trim_digest_display(secrets_dict)

  private, peekable, showable = [], [], []
  user_keys = set(['u:%d' % user.id] + ['r:%d' % role.id for role in user.roles])
  for digest, secret in new_dict:
    if user_keys & set(secret['reveal']):
      showable.append((digest, secret))
    elif user_keys & set(secret['peek']):
      peekable.append((digest, secret))
    else:
      private.append((digest, secret))

  content = []
  if showable:
    content.append((
      'Secrets you may `reveal`',
      '\n'.join(format_secret(digest, secret) for digest, secret in showable)))
  if peekable:
    content.append((
      'Secrets you may `peek` at',
      '\n'.join(format_secret(digest, secret) for digest, secret in peekable)))
  if private:
    content.append((
      'Secrets hidden from you',
      '\n'.join(format_secret(digest, secret) for digest, secret in private)))
  return content
