from .playset import PlaySet

async def setup(bot):
    await bot.add_cog(PlaySet())
