import asyncio
import random
from collections import Counter
from redbot.core.utils.chat_formatting import box
from redbot.core.data_manager import cog_data_path
import discord
import itertools
import numpy as np
import matplotlib.pyplot as pp
import pathlib
import os
import random
from zipfile import ZipFile

__all__ = ["SetSession"]

_CARD_SIZE = (84,61)
_LETTER_MAP = {"W":(0,0),"E":(0,1),"R":(0,2),"T":(0,3),"Y":(0,4),"U":(0,5),"I":(0,6),
               "S":(1,0),"D":(1,1),"F":(1,2),"G":(1,3),"H":(1,4),"J":(1,5),"K":(1,6),
               "Z":(2,0),"X":(2,1),"C":(2,2),"V":(2,3),"B":(2,4),"N":(2,5),"M":(2,6)}
#possible letters top-to-bottom, left-to-right for valid letter checking
_LETTERS = "WSZEDXRFCTGVYHBUJNIKM"



class SetSession:
    def __init__(self, ctx):
        self.dataDir = pathlib.Path(__file__).parent.resolve() / 'cards'
        self.ctx = ctx
        self.output_image_path = self.dataDir / f'board-{ctx.channel.id}.png'
        self.scores = Counter()
        self.deck = random.sample(range(81), 81)
        self.board = np.zeros((3,4),dtype=int)
        for i in range(self.board.size):
            self.board[i%3, i//3] = self.deck.pop(0)
        while not _board_contains_set(self.board):
            self.board = np.append(
                self.board,
                [[self.deck.pop(0)], [self.deck.pop(0)], [self.deck.pop(0)]],
                axis=1)
        self._gen_board_image()

    @classmethod
    def start(cls, ctx):
        session = cls(ctx)
        loop = ctx.bot.loop
        session._task = loop.create_task(session.run())
        return session

    async def run(self):
        await self._send_startup_msg()
        while True:
            await asyncio.sleep(2)
            f = discord.File(str(self.output_image_path))
            await self.ctx.send(file=f)
            foundSet = await self.wait_for_set()
            await self._update_board(foundSet)
            if _board_contains_set(self.board):
                self._gen_board_image()
            else:
                break

        await self.end_game()

    async def _send_startup_msg(self):
        await self.ctx.send("Starting Set. Type in the three card letters to call a set."
                            " Incorrect calls are -1 point. Good luck!")
        await asyncio.sleep(3)

    async def wait_for_set(self):
        self.foundSet = False
        self.wrongAnswers = []

        message = await asyncio.gather(
            self.ctx.bot.wait_for("message", check=self.check_set),
            self._wrong_handler())
        guess = message[0].content.upper()
        cards = []
        for i in range(3):
            cards.append(self.board[_LETTER_MAP[guess[i]]])
        self.scores[message[0].author] += 1
        await self.ctx.send(f"{message[0].author.display_name}: Set! +1 point")

        return cards

    async def _wrong_handler(self):
        while ((not self.foundSet) or self.wrongAnswers):
            if self.wrongAnswers:
                m = self.wrongAnswers.pop(0)
                self.scores[m.author] -= 1
                await self.ctx.send(f"{m.author.display_name}: not a set. -1 point")
            else:
                await asyncio.sleep(.25)

    def check_set(self, message: discord.Message):
        early_exit = message.channel != self.ctx.channel or message.author == self.ctx.guild.me
        if early_exit:
            return False
        guess = message.content.upper()
        if len(guess) != 3:
            return False
        validLetters = _LETTERS[:3*self.board.shape[1]]
        if set(guess) - set(validLetters):
            return False
        cards = [self.board[_LETTER_MAP[letter]] for letter in guess]
        if not _is_set(cards):
            self.wrongAnswers.append(message)
            return False
        self.foundSet = True
        return True

    #Given the cards to be removed from the board, generate the next board
    async def _update_board(self,cards):
        if (self.board.shape[1]>4) or (not self.deck):
            #try reducing
            oldBoard = self.board
            self.board = np.zeros((3,oldBoard.shape[1]-1),dtype=int)
            newi = 0
            for i in range(oldBoard.size):
                if oldBoard[i%3,i//3] not in cards:
                    self.board[newi%3,newi//3] = oldBoard[i%3,i//3]
                    newi += 1
        else:
            #replace missing cards
            for i in range(self.board.size):
                if self.board[i%3,i//3] in cards:
                    self.board[i%3,i//3] = self.deck.pop(0)

        while self.deck and not _board_contains_set(self.board):
            #repair boards while possible
            self.board = np.append(
                self.board,
                [[self.deck.pop(0)], [self.deck.pop(0)],[self.deck.pop(0)]],
                axis=1)

    async def end_game(self):
        """End the Set game and display scrores."""
        if self.scores:
            await self.send_table()
        self.stop()

    async def send_table(self):
        """Send a table of scores to the session's channel."""
        table = "+ Results: \n\n"
        for user, score in self.scores.most_common():
            table += f"+ {user}\t{score}\n"
        await self.ctx.send(box(table, lang="diff"))

    def stop(self):
        """Stop the Set session, without showing scores."""
        self.ctx.bot.dispatch("set_end", self)

    def force_stop(self):
        """Cancel whichever tasks this session is running."""
        self._task.cancel()
        channel = self.ctx.channel
        print(f"Force stopping Set session; {channel} in {channel.guild.id}")

    def _gen_board_image(self):
        image = np.zeros((self.board.shape[0]*_CARD_SIZE[1],
                          self.board.shape[1]*_CARD_SIZE[0],
                          4))
        for i in range(self.board.shape[0]):
            for j in range(self.board.shape[1]):
                v = _card_num_to_vec(self.board[i][j])
                imfile = f'{"".join(map(str, v))}.png'
                card = pp.imread(str(self.dataDir / imfile))
                image[i*_CARD_SIZE[1]:(i+1)*_CARD_SIZE[1],j*_CARD_SIZE[0]:(j+1)*_CARD_SIZE[0]]=card
        overlay = pp.imread(str(self.dataDir / 'overlay.png'))
        for i in range(image.shape[0]):
            for j in range(image.shape[1]):
                image[i][j][0] *= overlay[i][j][0]
                image[i][j][1] *= overlay[i][j][1]
                image[i][j][2] *= overlay[i][j][2]
        pp.imsave(str(self.output_image_path), image)

def _is_set(cardList):
    vecs = [_card_num_to_vec(card) for card in cardList]
    return all(sum(vecs[card_idx][trait_idx] for card_idx in range(3)) % 3 == 0
               for trait_idx in range(4))

def _board_contains_set(board):
    return any(_is_set([board[i%3, i//3], board[j%3, j//3], board[k%3, k//3]])
               for i, j, k in itertools.combinations(range(board.size), 3))

def _card_num_to_vec(cardNum):
    return [int(cardNum//27),int((cardNum//9)%3),int((cardNum//3)%3),int(cardNum%3)]
