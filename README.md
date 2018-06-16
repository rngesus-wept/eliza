This is a collection of cogs for Eliza, who is an instantiation of the [Red Discord bot](https://github.com/Cog-Creators/Red-DiscordBot). To make
these cogs available for your own Red instance, use

    [p]repo add eliza https://github.com/rngesus-wept/eliza
    [p]cog update
    [p]cog install eliza $COG_NAME

Throughout this documentation, when Eliza is said to listen for a message, she
is listening only in the same channel that the relevant command was issued, with
a timeout of 5 minutes.


# **faq**

Maintain a per-guild database of frequently asked questions (FAQs). FAQs are managed by moderators,
and are searchable only by moderator-defined tags (i.e. not by question nor answer text). Eliza uses
this cog for rulings associated with games popular on the guilds she oversees.

A moderator can begin the creation of an FAQ entry using `[p]faq new <question>`. If `<question>` is
omitted, Eliza will instead listen for the next message from the invoking user, and take that as the
question. (This latter method of question creation is suggested, as discord.py's input parsing may
get stuck on questions containing special characters like `(` and `"`.) Regardless of how the question
was entered, Eliza will then listen for the next message from the invoking user and take that input
as the answer to the question, confirming entry creation in an embed with the entry's ID.

Existing entries can be further modified by a moderator, using `[p]faq edit-q <id>` and
`[p]faq edit-a <id>` to change the entry's question and answer respectively, and
`[p]faq tag <id> <tag1> [<tag2>...]` to add tags to the entry. Multi-word tags should be contained
in quotes. <del>Tags beginning with a hyphen `-` are instead removed from the entry's tag list, e.g.
 `[p]faq tag 10 -wrong`.</del> *This feature isn't working correctly for now; see issue #5.*

Users can request that Eliza show a FAQ entry by using `[p]faq search <tag1> [<tag2>...]` to list all
entries that have *all* the listed tags; or by using `[p]faq show <id>` to show specific entry.
