import logging

import aiohttp
import discord
from discord.ext import commands

from source import utilities, dataclass

log: logging.Logger = utilities.getLog("Cog::base")


class Base(commands.Cog):
    """Configuration commands"""

    def __init__(self, bot: dataclass.Bot):
        self.bot = bot

        self.slash = bot.slash

        self.emoji = "🚩"

    @commands.command(name="Shutdown", brief="Shuts down the bot")
    async def cmdShutdown(self, ctx: commands.Context):
        if await self.bot.is_owner(ctx.author):
            log.warning("Shutdown called")
            await ctx.send("Shutting down 🌙")
            await self.bot.close()

    @commands.command(name="setname", brief="Renames the bot")
    async def cmdSetName(self, ctx: commands.Context, name: str):
        if await self.bot.is_owner(ctx.author):
            await self.bot.user.edit(username=name)
            await ctx.send(f"Set name to {name}")

    @commands.command(name="setAvatar", brief="Sets the bots avatar")
    async def cmdSetAvatar(self, ctx: commands.Context):
        if await self.bot.is_owner(ctx.author):
            if ctx.message.attachments:
                photo = ctx.message.attachments[0].url
                async with aiohttp.ClientSession() as session:
                    async with session.get(photo) as r:
                        if r.status == 200:
                            data = await r.read()
                            try:
                                await self.bot.user.edit(avatar=data)
                                return await ctx.send("Set avatar, how do i look?")
                            except discord.HTTPException:
                                await ctx.send("Unable to set avatar")
                                return
            await ctx.send("I cant read that")


def setup(bot):
    """Called when this cog is mounted"""
    bot.add_cog(Base(bot))
    log.info("Base mounted")


def teardown(bot):
    """Called when this cog is unmounted"""
    log.warning('Base un-mounted')
    for handler in log.handlers[:]:
        log.removeHandler(handler)
