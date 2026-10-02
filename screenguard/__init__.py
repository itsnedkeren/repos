from .screenguard import ScreenGuard


async def setup(bot):
    await bot.add_cog(ScreenGuard(bot))
