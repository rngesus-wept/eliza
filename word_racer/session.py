import asyncio
from collections import Counter
from redbot.core.utils.chat_formatting import box
from redbot.core.data_manager import cog_data_path
import discord
import numpy as np
import pathlib
import re
from PIL import Image, ImageDraw, ImageFont

__all__ = ["WordRacerSession"]

_LEVEL_COUNT = 4

_LEVELS = ["#######....##....##....##....#######",
           "##..###....#............#....###..##",
           "....##....##............##....##....",
           "..............##....##.............."]

_FREQ = [[142,25,66,53,218,15,42,34,168,1,8,88,44,130,124,45,1,135,180,125,50,10,7,2,23,4],
         [130,27,65,53,199,17,42,35,154,2,10,86,44,118,114,45,2,123,164,115,50,12,9,3,24,6],
         [119,29,64,53,181,19,43,37,142,3,13,84,45,108,104,46,3,112,150,105,51,15,12,5,26,8],
         [100,33,62,53,150,24,44,40,120,9,21,80,46,90,87,47,9,93,125,88,52,23,20,12,30,16]]

_BONUSES = [{}, {(0,2):2,(5,3):2}, {(0,0):3,(5,5):3}, {(0,0):3,(0,5):2,(5,0):2,(5,5):3}]

class WordRacerSession:
    def __init__(self, ctx):
        self.level = 0
        self.dataDir = pathlib.Path(__file__).parent.resolve() / 'data'
        
        # feel free to experiment with this
        self.dictDir = self.dataDir/"dict/enable2k.txt"
        self.fontDir = self.dataDir/"fonts/Roboto-Medium.ttf"

        self.ctx = ctx
        self.output_image_path = self.dataDir / f'board-{ctx.channel.id}.png'
        self.scores = Counter()
        self.round_scores = Counter()
        self.claims = {}
        self.valid_words = Counter()
        self.board = [["_" for _ in range(6)] for __ in range(6)]
        self.bonus = {}
        self.nrows = 6
        self.ncols = 6
        self.round_finish = False
        self.reaction_queue = []

    @classmethod
    def start(cls, ctx):
        session = cls(ctx)
        loop = ctx.bot.loop
        session._task = loop.create_task(session.run())
        return session

    async def run(self):
        
        await self._send_startup_msg()

        # Round loop
        while self.level < _LEVEL_COUNT:
            # Round setup
            self.claims = {}
            self.valid_words = Counter()
            self.round_scores = Counter()
            self._gen_board()
            self._get_score_dict()
            self._gen_image()
            await asyncio.sleep(3)

            # send board image
            f = discord.File(str(self.output_image_path))
            await self.ctx.send(f"Starting round {self.level+1}. {len(self.valid_words)} words to find.",file=f)

            # Message handler for round
            self.round_finish = False
            await self.run_round()

            # Round cleanup
            await self.finish_round()
            self.level += 1
            
        await self.end_game()

    async def _send_startup_msg(self):
        await self.ctx.send("Starting Word Racer. Find words boggle-style and gain points."
                            " Incorrect calls are -1 point. In rounds 2-4 there are bonuses:"
                            " blue is 2x points and red is 3x points (they multiplicatively stack)."
                            " Good luck!")

    async def run_round(self):
        timer_task = self.ctx.bot.loop.create_task(self.timer_task())
        reaction_task = self.ctx.bot.loop.create_task(self.reactions_handler())
        try:
            await self.ctx.bot.wait_for("message", check=self.check_message, timeout=120)
        except asyncio.TimeoutError:
            #Round over
            pass
        finally:
            timer_task.cancel()
            reaction_task.cancel()


    async def reactions_handler(self):
        while not self.round_finish or self.reaction_queue:
            if self.reaction_queue:
                m = self.reaction_queue.pop(0)
                await m[0].add_reaction(m[1])
            else:
                await asyncio.sleep(.25)

    async def finish_round(self):
        await self.send_round_table(f"- Round {self.level+1} over! \n Round scores:")
        if self.level in [1,2]:
            await self.send_table("Total scores so far:")
        
        table = ""
        for word, score in self.valid_words.most_common():
            claim = "none"
            if word in self.claims:
                claim = self.claims[word]
            table += f"{word}\t{score}\t{claim}\n"
            if len(table) > 1900:
                await self.ctx.send(box(table, lang="diff"))
                table = ""
        if table != "":
            await self.ctx.send(box(table, lang="diff"))
    
    async def timer_task(self):
        reveal = 3
        while reveal:
            await asyncio.sleep(30)
            msg = f"{30*reveal} seconds remaining in round {self.level+1}. {len(self.valid_words)-len(self.claims)} words left to find."
            await self.send_round_table(msg)
            reveal -= 1
        await asyncio.sleep(30)

    def check_message(self, message: discord.Message):
        early_exit = message.channel != self.ctx.channel or message.author == self.ctx.guild.me
        if early_exit:
            return
        guess = message.content.lower()
        if not re.match(r"[a-z]{3,}", guess): # check if guess is a string of letters
            return
        if guess not in self.valid_words:
            # Wrong answer handling
            self.reaction_queue.append((message, "\N{CROSS MARK}"))
            self.scores[message.author] -= 1
            self.round_scores[message.author] -= 1
            return
        if guess in self.claims:
            # Slow answer handling
            self.reaction_queue.append((message, "\U0001f501"))
            return
        # Correct answer handling
        self.claims[guess] = message.author
        self.scores[message.author] += self.valid_words[guess]
        self.round_scores[message.author] += self.valid_words[guess]
        self.reaction_queue.append((message, "\N{WHITE HEAVY CHECK MARK}"))
        return 
        
    async def end_game(self):
        """End the game and display scores."""
        if self.scores:
            await self.send_table("Final results:")
        self.stop()

    async def send_table(self, msg):
        """Send a table of scores to the session's channel."""
        table = f"+ {msg} \n\n"
        for user, score in self.scores.most_common():
            table += f"+ {user}\t{score}\n"
        await self.ctx.send(box(table, lang="diff"))
    
    async def send_round_table(self, msg):
        """Send a table of round scores to the session's channel."""
        table = f"+ {msg} \n\n"
        for user, score in self.round_scores.most_common():
            table += f"+ {user}\t{score}\n"
        f = discord.File(str(self.output_image_path))
        await self.ctx.send(box(table, lang="diff"), file=f)

    def stop(self):
        """Stop the wordracer session, without showing scores."""
        self.ctx.bot.dispatch("wordracer_end", self)

    def force_stop(self):
        """Cancel whichever tasks this session is running."""
        self._task.cancel()
        channel = self.ctx.channel
        print(f"Force stopping Wordracer session; {channel} in {channel.guild.id}")

    def _gen_image(self):
        BACKGROUND_COLOR = (206,206,156,255)
        NORMAL_COLOR = (156,156,99,255)
        DOUBLE_COLOR = (132,132,255,255)
        TRIPLE_COLOR = (255,132,132,255)
        LINE_COLOR = (0,0,0,255)

        img = Image.new("RGBA", (384,384), BACKGROUND_COLOR)

        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(str(self.fontDir), 16)

        for i in range(36):
            col = NORMAL_COLOR
            x = i//6
            y = i%6
            if (y,x) in self.bonus:
                if self.bonus[(y,x)] == 2:
                    col = DOUBLE_COLOR
                if self.bonus[(y,x)] == 3:
                    col = TRIPLE_COLOR
            if self.board[x][y] != "#":
                if y != 5 and self.board[x][y+1] != "#":
                    d.line([64*x+32,64*y+32,64*x+32,64*y+96], fill=LINE_COLOR, width=1)
                if y != 5 and x != 5 and self.board[x+1][y+1] != "#":
                    d.line([64*x+32,64*y+32,64*x+96,64*y+96], fill=LINE_COLOR, width=1)
                if x != 5 and self.board[x+1][y] != "#":
                    d.line([64*x+32,64*y+32,64*x+96,64*y+32], fill=LINE_COLOR, width=1)
                if y != 0 and x != 5 and self.board[x+1][y-1] != "#":
                    d.line([64*x+32,64*y+32,64*x+96,64*y-32], fill=LINE_COLOR, width=1)
                d.ellipse([64*x+16,64*y+16,64*x+48,64*y+48], fill=col)
                txt = self.board[x][y][0].upper()+self.board[x][y][1:]
                w, h = d.textsize(txt, font=f)
                d.text((64*x+32-w/2, 64*y+32-h/2), txt, fill=LINE_COLOR, font=f)
        img.save(str(self.output_image_path), "PNG")

    def _gen_board(self):
        def generate_letter(level):
            prob = np.array(_FREQ[level])
            prob = prob.astype(float)/sum(prob)
            l = chr(ord('a')+np.random.choice(26,p=prob))
            if l == "q":
                l = "qu"
            return l

        levelmap = _LEVELS[self.level]
        self.bonus = _BONUSES[self.level]
        for i in range(36):
            if levelmap[i] == "#":
                self.board[i//6][i%6] = "#"
            else:
                self.board[i//6][i%6] = generate_letter(self.level)

    def _solve_init(self):
        # Return generator of words found
        alphabet = ''.join(set(''.join([''.join(i) for i in self.board])))
        bogglable = re.compile('[' + alphabet + ']{3,}$', re.I).match

        words = set(word.lower().rstrip('\n') for word in open(str(self.dictDir)) if bogglable(word))
        prefixes = set(word[:i] for word in words
                    for i in range(2, len(word)+1))

        def solve():
            for y, row in enumerate(self.board):
                for x, letter in enumerate(row):
                    for result in extending(letter, ((x, y),)):
                        yield result

        def extending(prefix, path):
            if prefix in words:
                yield (prefix, path)
            for (nx, ny) in neighbors(path[-1][0], path[-1][1]):
                if (nx, ny) not in path:
                    prefix1 = prefix + self.board[ny][nx]
                    if prefix1 in prefixes:
                        for result in extending(prefix1, path + ((nx, ny),)):
                            yield result

        def neighbors(x, y):
            for nx in range(max(0, x-1), min(x+2, self.ncols)):
                for ny in range(max(0, y-1), min(y+2, self.nrows)):
                    yield (nx, ny)

        return solve()

    def _get_score_dict(self):
        def score_word(x):
            x = len(x)
            if x <= 6:
                return (x-2)*(x-3)*5+10
            else:
                return x*40-170

        for i in self._solve_init():
            score = score_word(i[0])
            for c in i[1]:
                if c in self.bonus:
                    score *= self.bonus[c]
            if i[0] in self.valid_words:
                if score > self.valid_words[i[0]]:
                    self.valid_words[i[0]] = score
            else:
                self.valid_words[i[0]] = score