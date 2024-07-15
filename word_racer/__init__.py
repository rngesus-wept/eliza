from .wordracer import WordRacer

async def setup(bot):
    await bot.add_cog(WordRacer())
