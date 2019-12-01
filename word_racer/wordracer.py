from redbot.core import commands
import discord
from .session import WordRacerSession
from redbot.core import Config
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.chat_formatting import box, pagify

UNIQUE_ID = 0xED5931AC

_ = Translator("WordRacer", __file__)

class WordRacer(commands.Cog):
    def __init__(self):
        super().__init__()
        self.wordracer_sessions = []
        self.conf = Config.get_conf(self, identifier=UNIQUE_ID, force_registration=True)
        self.conf.register_member(wins=0, games=0, total_score=0)
 
    @commands.group(invoke_without_command=True)
    async def wordracer(self, ctx: commands.Context):
        """
        Discord port for the defunct Yahoo! Games "Word Racer".
        
        Find words in the grid Boggle-style. That is, find a sequence of connected letters,
        where you may pick letters horizontally, vertically, or diagonally, but you may only
        use each space once. You have to be fast, however - only the first person to call out
        a valid word can claim the points!

        The longer the word, the more points it gives. Starting from the second round, there are
        also colored bonus letters. If you use these letters into your word, then these points
        may be multiplied! Blue gives you double points and red gives you triple points.
        Getting multiple bonuses works multiplicatively! (so e.g. two 2x = 4x) There may also 
        be a small penalty for guessing words not in the dictionary.

        Highest score wins! Good luck!
        """
        session = self._get_wordracer_session(ctx.channel)
        if session is not None:
            await ctx.send("There is already an ongoing Word Racer session in this channel.")
            return
        session = WordRacerSession.start(ctx)
        self.wordracer_sessions.append(session)
        print("New Word Racer session; "+str(ctx.channel)+" in "+str(ctx.guild.id))
        
    @wordracer.command(name="stop", aliases=["cancel"])
    async def wordracer_stop(self, ctx: commands.Context):
        """Stop an ongoing Word Racer session."""
        session = self._get_wordracer_session(ctx.channel)
        if session is None:
            await ctx.send("There is no ongoing Word Racer session in this channel.")
            return
        await session.end_game()
        session.force_stop()
        await ctx.send("Word Racer stopped.")
        
    @staticmethod
    def _get_sort_key(key: str):
        key = key.lower()
        if key in ("wins", "average_score", "total_score", "games"):
            return key
        elif key in ("avg", "average"):
            return "average_score"
        elif key in ("total", "score", "answers", "correct"):
            return "total_score"
    @wordracer.group(
        name="leaderboard", aliases=["lboard"], autohelp=False, invoke_without_command=True
    )
    async def wordracer_leaderboard(self, ctx: commands.Context):
        """Leaderboard for wordracer.
        Defaults to the top 10 of this server, sorted by total wins. Use
        subcommands for a more customised leaderboard.
        """
        cmd = self.wordracer_leaderboard_server
        await ctx.invoke(cmd, "wins", 10)
    
    @wordracer_leaderboard.command(name="server")
    @commands.guild_only() 
    async def wordracer_leaderboard_server(
        self, ctx: commands.Context, sort_by: str = "wins", top: int = 10
    ):
        """Leaderboard for this server.
        `<sort_by>` can be any of the following fields:
         - `wins`  : total wins
         - `avg`   : average score
         - `total` : total correct answers
         - `games` : total games played
        `<top>` is the number of ranks to show on the leaderboard.
        """
        key = self._get_sort_key(sort_by)
        if key is None:
            await ctx.send(
                _(
                    "Unknown field `{field_name}`, see `{prefix}help wordracer leaderboard server` "
                    "for valid fields to sort by."
                ).format(field_name=sort_by, prefix=ctx.prefix)
            )
            return
        guild = ctx.guild
        data = await self.conf.all_members(guild)
        data = {guild.get_member(u): d for u, d in data.items()}
        data.pop(None, None)  # remove any members which aren't in the guild
        await self.send_leaderboard(ctx, data, key, top)
        
    async def send_leaderboard(self, ctx: commands.Context, data: dict, key: str, top: int):
        """Send the leaderboard from the given data.
        Parameters
        ----------
        ctx : commands.Context
            The context to send the leaderboard to.
        data : dict
            The data for the leaderboard. This must map `discord.Member` ->
            `dict`.
        key : str
            The field to sort the data by. Can be ``wins``, ``total_score``,
            ``games`` or ``average_score``.
        top : int
            The number of members to display on the leaderboard.
        Returns
        -------
        `list` of `discord.Message`
            The sent leaderboard messages.
        """
        if not data:
            await ctx.send(_("There are no scores on record!"))
            return
        leaderboard = self._get_leaderboard(data, key, top)
        ret = []
        for page in pagify(leaderboard, shorten_by=10):
            ret.append(await ctx.send(box(page, lang="py")))
        return ret
        
    @staticmethod
    def _get_leaderboard(data: dict, key: str, top: int):
        # Mix in average score
        for member, stats in data.items():
            if stats["games"] != 0:
                stats["average_score"] = stats["total_score"] / stats["games"]
            else:
                stats["average_score"] = 0.0
        # Sort by reverse order of priority
        priority = ["average_score", "total_score", "wins", "games"]
        try:
            priority.remove(key)
        except ValueError:
            raise ValueError(f"{key} is not a valid key.")
        # Put key last in reverse priority
        priority.append(key)
        items = data.items()
        for key in priority:
            items = sorted(items, key=lambda t: t[1][key], reverse=True)
        max_name_len = max(map(lambda m: len(str(m)), data.keys()))
        # Headers
        headers = (
            "Rank",
            "Member" + " " * (max_name_len - 6),
            "Wins",
            "Games Played",
            "Total Score",
            "Average Score",
        )
        lines = [" | ".join(headers), " | ".join(("-" * len(h) for h in headers))]
        # Header underlines
        for rank, tup in enumerate(items, 1):
            member, m_data = tup
            # Align fields to header width
            fields = tuple(
                map(
                    str,
                    (
                        rank,
                        member,
                        m_data["wins"],
                        m_data["games"],
                        m_data["total_score"],
                        round(m_data["average_score"], 2),
                    ),
                )
            )
            padding = [" " * (len(h) - len(f)) for h, f in zip(headers, fields)]
            fields = tuple(f + padding[i] for i, f in enumerate(fields))
            lines.append(" | ".join(fields).format(member=member, **m_data))
            if rank == top:
                break
        return "\n".join(lines)
        
    @commands.Cog.listener()
    async def on_wordracer_end(self, session: WordRacerSession):
        """Event for a WordRacer session ending.
        This method removes the session from this cog's sessions, and
        cancels any tasks which it was running.
        Parameters
        ----------
        session : WordRacerSession
            The session which has just ended.
        """
        channel = session.ctx.channel
        print("Ending Word Racer session; "+str(channel)+" in "+str(channel.guild.id))
        if session in self.wordracer_sessions:
            self.wordracer_sessions.remove(session)
        if session.scores:
            await self.update_leaderboard(session)
            
    async def update_leaderboard(self, session):
        """Update the leaderboard with the given scores.
        Parameters
        ----------
        session : WordRacerSession
            The WordRacer session to update scores from.
        """
        max_score = 0
        for member, score in session.scores.items():
            if score>max_score:
                max_score=score
                
        for member, score in session.scores.items():
            if member.id == session.ctx.bot.user.id:
                continue
            stats = await self.conf.member(member).all()
            if score == max_score:
                stats["wins"] += 1
            stats["total_score"] += score
            stats["games"] += 1
            await self.conf.member(member).set(stats)
            
    def _get_wordracer_session(self, channel: discord.TextChannel) -> WordRacerSession:
        return next(
            (session for session in self.wordracer_sessions if session.ctx.channel == channel), None
        )