"""Cog for managing tagged FAQs within a channel."""

import discord
from discord.ext import commands

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
