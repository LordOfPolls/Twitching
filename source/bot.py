import asyncio
import logging
import re
import traceback
from datetime import datetime
from random import choice

import discord
import discord_slash
from discord.ext import commands
from discord_slash import SlashCommand, SlashContext, error

from . import utilities, dataclass

log: logging.Logger = utilities.getLog("Bot", level=logging.DEBUG)
intents = discord.Intents.default()
intents.members = True

bot = dataclass.Bot(
    command_prefix="twitching ",
    description="A twitch notifs bot",
    case_insensitive=True,
    intents=intents,
    cogList=[
        'source.cogs.base',
        'source.cogs.twitch'
    ],
    help_command=None
)
slash = SlashCommand(bot, sync_commands=False, override_type=True)  # register a slash command system

slash.logger = utilities.getLog("slashAPI", logging.DEBUG)
bot.perms = "24640"


def run():
    if bot.cogList:
        log.info("Mounting cogs...")
        for cog in bot.cogList:
            log.spam(f"Mounting {cog}...")
            bot.load_extension(cog)
    else:
        log.warning("No cogs to load!")
    log.info("Connecting to discord...")
    bot.run(utilities.getCredential("botToken"), bot=True, reconnect=True)


async def startupTasks():
    """All the tasks the bot needs to run when it starts up"""
    log.debug("Running startup tasks...")
    bot.appInfo = await bot.application_info()
    bot.startTime = datetime.now()
    await bot.change_presence(status=discord.Status.do_not_disturb, activity=discord.Game("Startup"))

    log.info("Establishing connection to database...")
    try:
        await bot.db.connect()
    except Exception as e:
        log.error(e)

    log.info("Running cog setup tasks")
    for cog in bot.cogs:
        _c = bot.get_cog(cog)
        if hasattr(_c, "setup"):
            await _c.setup()


@bot.event
async def on_ready():
    """Called when the bot is ready"""
    if not bot.startTime:
        await startupTasks()
    log.info("INFO".center(40, "-"))
    log.info(f"Logged in as       : {bot.user.name} #{bot.user.discriminator}")
    log.info(f"User ID            : {bot.user.id}")
    log.info(f"Start Time         : {bot.startTime.ctime()}")
    log.info(f"DB Connection Type : "
             f"{'Tunneled' if bot.db.tunnel and bot.db.dbPool else 'Direct' if bot.db.dbPool else 'Not Connected'}")
    log.info(f"Server Count       : {len(bot.guilds)}")
    log.info(f"Cog Count          : {len(bot.cogs)}")
    log.info(f"Command Count      : {len(slash.commands)}")
    log.info(f"Discord.py Version : {discord.__version__}")
    log.info("END-INFO".center(40, "-"))

    await bot.change_presence(status=discord.Status.online,
                              activity=discord.Activity(type=discord.ActivityType.watching, name="Twitch"))


@bot.event
async def on_slash_command(ctx: SlashContext):
    subcommand = ""
    try:
        if ctx.subcommand_name:
            subcommand = ctx.subcommand_name
    except AttributeError:
        pass
    if ctx.guild:
        log.info(f"CMD - {ctx.guild.id}::{ctx.author.id}: {ctx.command} {subcommand}")
    else:
        log.info(f"CMD - Direct Message::{ctx.author.id}: {ctx.command} {subcommand}")


@bot.event
async def on_command_error(ctx, ex):
    return


@bot.event
async def on_slash_command_error(ctx, ex):
    def logError():
        log.error('Ignoring exception in command {}: {}'.format(ctx.command,
                                                                "".join(traceback.format_exception(type(ex), ex,
                                                                                                   ex.__traceback__))))

    if isinstance(ex, commands.errors.CommandOnCooldown):
        lines = [
            "Whoa",
            "Damn you're eager",
            "Too... many... tags",
            "Gotta go fast huh?",
            "Speed is key",
            "Where's that damn forth chaos emerald",
            "You know what they say, the more the merrier",
            "Oh no",
            "Spam time?"
            ""
        ]
        remaining = re.search(r'\d+\.', str(ex))
        await ctx.send(f"`{choice(lines)}`\n"
                       f"You're making streamers too fast. Wait {remaining.group().replace(',', 's')} before using that again ")
    elif isinstance(ex, discord.errors.Forbidden):
        log.error(f"Missing permissions in {ctx.guild.name}")
        await ctx.send(f"**Error:** I am missing permissions.\n"
                       f"Please make sure i can access this channel, manage messages, embed links, and add reactions.")
    elif isinstance(ex, discord_slash.error.CheckFailure):
        log.debug(f"Ignoring command: check failure")
    elif isinstance(ex, discord.NotFound):
        logError()
        await ctx.send("Discord did not send the interaction correctly, this usually resolves after a few minutes, "
                       "if it doesnt, please use `/server` and report it")
    else:
        logError()
        await ctx.send("An un-handled error has occurred, and has been logged, please try again later.\n"
                       "If this continues please use `/server` and report it in my server")


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Called when bot is added to a guild"""
    while not bot.is_ready():
        await asyncio.sleep(5)
    log.info(f"Joined Guild {guild.id}. {len([m for m in guild.members if not m.bot])} users")


@bot.event
async def on_guild_remove(guild):
    while not bot.is_ready():
        await asyncio.sleep(5)
    if guild.id == 110373943822540800:
        return
    log.info(f"Left Guild {guild.id} || Purging data...")


@bot.event
async def on_member_join(member):
    if member.guild.id == 110373943822540800:
        return
    if not member.bot:
        log.spam("Member added event")


@bot.event
async def on_member_remove(member):
    if member.guild.id == 110373943822540800:
        return
    if not member.bot:
        log.spam("Member removed event")
