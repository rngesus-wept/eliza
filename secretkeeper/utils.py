import copy
import discord
import re


MAX_FIELD_CAPACITY = 1024
RE_HANGING_PARAGRAPHS = re.compile(r'\n(?=\S)')


class UtilityError(Exception):
  """Base class for utility-related exceptions."""
  def __init__(self, message):
    self.message = message


class OverlongEmbedComponentError(UtilityError):
  def __init__(self, item):
    super().__init__(
      'Desired embed component has length %d exceeding %d:\n%r' % (
        len(item), MAX_FIELD_CAPACITY, item))


def paginated_embed(*,
                    fields,
                    break_re=RE_HANGING_PARAGRAPHS,
                    **embed_kwargs):
  """Generate a list of embeds, resulting from the pagination of FIELDS content.

  Args:
    fields - A list of `(field_name, field_value)` pairs indicating the desired
        embed content. (Note that `""` is a valid field_name.)
    break_re - A regex object (or a string denoting a regex) used to break
        `field_value`s into smaller pieces for pagination. It defaults to
        `r'\\n(?=\\S)'`, which breaks paragraphs with hanging indentation.
    embed_kwargs - Valid keyword args for `discord.Embed`.
  Returns:
    A list of embeds, containing paginated `field_value` content, with
        `field_name`s rewritten to indicate the number of pages they've been
        broken into."""

  embed_fields, capacity = [[]], MAX_FIELD_CAPACITY
  # embed_fields is a list of embed contents, where each embed's contents is
  # represented as a list of (field_name, field_value) pairs
  for field_name, field_value in fields:
    field_segments = [[]]
    for field_piece in re.split(break_re, field_value):
      if len(field_piece) > MAX_FIELD_CAPACITY:
        raise OverlongEmbedComponentError(field_piece)
      if len(field_piece) < capacity:
        field_segments[-1].append(field_piece)
        capacity -= (len(field_piece) + 1)  # include eventual newline separator
      else:
        # Fit the piece into a new field segment
        field_segments.append([field_piece])
        capacity = MAX_FIELD_CAPACITY - len(field_piece)
    if len(field_segments) == 1:
      embed_fields[-1].append((field_name, '\n'.join(field_segments[0])))
    else:
      embed_fields[-1].append(('%s (1/%d)' % (field_name, len(field_segments)),
                               '\n'.join(field_segments[0])))
      embed_fields.extend(
        [('%s (%d/%d)' % (field_name, segment_idx + 1, len(field_segments)),
          '\n'.join(content))]
        for segment_idx, content in enumerate(field_segments[1:], start=1))

  embeds = []
  for content in embed_fields:
    new_embed = discord.Embed(**embed_kwargs)
    for name, value in content:
      new_embed.add_field(name=name, value=value, inline=False)
    embeds.append(new_embed)
  return embeds
