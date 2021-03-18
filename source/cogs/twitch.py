import asyncio
import concurrent.futures
import functools
import json
import logging
import time
import traceback
import typing

import discord
from discord.ext import commands, tasks
from discord_slash import cog_ext, SlashContext
from discord_slash.utils import manage_commands
from twitchAPI import Twitch as TwitchAPI
from twitchAPI.types import TwitchAuthorizationException

from source import utilities, dataclass

log: logging.Logger = utilities.getLog("Cog::twitch")


class SetEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        return json.JSONEncoder.default(self, obj)


class Twitch(commands.Cog):
    """Configuration commands"""

    def __init__(self, bot: dataclass.Bot):
        self.bot = bot

        self.slash = bot.slash

        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.twitch = TwitchAPI(app_id=utilities.getCredential("twitchAppID"),
                                app_secret=utilities.getCredential("twitchSecret"))
        self.emoji = "📺"

    async def setup(self):
        try:
            log.debug("Authenticating Twitch")
            self.twitch.authenticate_app([])
        except TwitchAuthorizationException:
            log.critical("Failed to authenticate with twitch, abort")
            await self.bot.close()
        else:
            log.info("Authenticated with Twitch")
        self.checkStatus.start()

    def check_perms(self, ctx):
        """Checks if user can use these commands"""
        if ctx.author.id == ctx.guild.owner.id:
            return True

        base = discord.Permissions.none()

        for r in ctx.author.roles:
            base.value |= r.permissions.value

        if base.administrator or base.manage_messages or base.manage_guild:
            return True

        if base.manage_messages:
            return True
        return False

    async def archiveTwitchChannel(self, twitchChannel: str):
        """Checks if messages should be archived, and if so, archives them"""
        data = await self.bot.db.execute(
            f"SELECT * FROM twitching.streams WHERE twitchChannel = '{twitchChannel}'",
            getOne=True
        )
        if data is not None:
            # user is no longer streaming, and as data is still here, we need to archive
            if data['postedMessages'] is None:
                return None

            # posted messages stored like this:
            # [channelID, messageID]
            log.debug(f"{twitchChannel} has likely stopped streaming, archiving")
            for msgObj in json.loads(data['postedMessages']):
                try:
                    channel = self.bot.get_channel(int(msgObj[0]))
                    if channel:
                        message: discord.Message = await self.bot.getMessage(int(msgObj[1]), channel)
                        if message:
                            originEmbed: discord.Embed = message.embeds[0]
                            embed = discord.Embed(colour=discord.Colour.dark_grey())
                            author = originEmbed.author.__dict__
                            embed.description = originEmbed.description.replace("Tune In", "View Channel")
                            embed.set_author(name=author['name'].replace("is live", "was live"),
                                             icon_url=author['icon_url'])
                            await message.edit(embed=embed)
                except Exception as ex:
                    log.error('Ignoring exception in twitch: {}'.format(
                        "".join(traceback.format_exception(type(ex), ex,
                                                           ex.__traceback__))))

            # i dont know if twitch re-uses streamIDs but im going to be careful
            await self.bot.db.execute(
                f"DELETE FROM twitching.streams WHERE streamID = '{data['streamID']}'",
                getOne=True
            )

    async def storeMessage(self, streamID: str, message: discord.Message, twitchChannel: str):
        """Stores posted stream notifications so they can be archived later"""
        data = await self.bot.db.execute(
            f"SELECT * FROM twitching.streams WHERE streamID='{streamID}'",
            getOne=True
        )
        if data is not None:
            postedMessages = json.loads(data['postedMessages']) if data['postedMessages'] is not None else []
        else:
            postedMessages = []

        # prevent duplication, shouldn't happen, but i wanna be sure
        data = [message.channel.id, message.id]
        if data not in postedMessages:
            postedMessages.append(data)

        postedMessages = await self.bot.db.escape(json.dumps(postedMessages))

        await self.bot.db.execute(
            f"INSERT INTO twitching.streams (streamID, postedMessages, twitchChannel) "
            f"VALUES ('{streamID}', '{postedMessages}', '{twitchChannel}') "
            f"ON DUPLICATE KEY UPDATE postedMessages = '{postedMessages}'"
        )

    @tasks.loop(minutes=1)
    async def checkStatus(self):
        try:
            postedStreams = set()
            for guild in self.bot.guilds:
                seenIDs = set()
                guildData = await self.bot.db.execute(
                    f"SELECT * FROM twitching.twitch WHERE guildID = '{guild.id}'",
                    getOne=True
                )
                if guildData is None:
                    continue

                if guildData['postChannel'] is not None and guildData['twitchChannel'] is not None:
                    twitchChannels: set = set(json.loads(guildData['twitchChannel']))
                    postedStreams: set = \
                        set(json.loads(guildData['postedStreamIDs'])) if guildData[
                                                                             'postedStreamIDs'] is not None else set()

                    # obtain all channels for this guild in one api call
                    # this limits it to 100 channels per guild, but if someone is trying to track more than that
                    # they are almost certainly trying to break the bot
                    done, pending = await asyncio.wait(
                        fs={
                            self.bot.loop.run_in_executor(
                                self.executor,
                                functools.partial(self.twitch.get_users, user_ids=list(twitchChannels))
                            )
                        }
                    )
                    allUserData = [task.result() for task in done][0]

                    for tChannel in twitchChannels:
                        userData = [d for d in allUserData['data'] if d['id'] == tChannel]
                        done, pending = await asyncio.wait(
                            fs={
                                self.bot.loop.run_in_executor(
                                    self.executor,
                                    functools.partial(self.twitch.get_streams, user_id=tChannel)
                                )
                            }
                        )
                        streamData = [task.result() for task in done][0]
                        userData = userData[0] if userData else None
                        streamData = streamData['data'] if streamData else None

                        if userData is None:
                            # user is no longer on twitch
                            continue

                        if not streamData:
                            # User is not streaming check if they were, and archive
                            await self.archiveTwitchChannel(userData['login'])
                            continue

                        streamData = streamData[0]
                        seenIDs.add(streamData['id'])

                        # User is streaming
                        if streamData['id'] not in postedStreams:
                            log.info(f"{userData['display_name']} is live, and stream is new, posting")
                            channel = guild.get_channel(int(guildData['postChannel']))
                            if channel:
                                thumbnailURL = streamData['thumbnail_url']
                                thumbnailURL = thumbnailURL.replace("{width}", "1280")
                                thumbnailURL = thumbnailURL.replace("{height}", "720")
                                colour = await utilities.getDominantColour(self.bot, userData['profile_image_url'])

                                embed = discord.Embed(colour=colour)
                                embed.description = f"{streamData['title']}\n" \
                                                    f"[Tune In](https://twitch.tv/{userData['login']})"
                                embed.set_author(name=f"{userData['display_name']} is live",
                                                 icon_url=userData['profile_image_url'])
                                embed.set_image(url=thumbnailURL + f"?{round(time.time())}")
                                embed.url = f"https://twitch.tv/{userData['login']}"

                                # if we're supposed to be mentioning a role
                                if guildData['mentions']:
                                    mentions: dict = json.loads(guildData['mentions'])
                                    if tChannel in mentions or "all" in mentions:
                                        # user has probably set a channel to mention
                                        role: str = mentions[tChannel] if tChannel in mentions else mentions['all']
                                        role: discord.Role = await guild.get_role(int(role))
                                        if role:
                                            embed.description = f"{embed.description}\n{role.mention}"

                                msg = await channel.send(embed=embed)

                                await self.storeMessage(streamData['id'], msg, userData['login'])

                                postedStreams.add(streamData['id'])
                        else:
                            log.spam(f"{userData['display_name']} is live, but stream is old, not posting")
                # remove ended streams
                for s in postedStreams.copy():
                    if s not in seenIDs:
                        # this stream is over, remove it
                        postedStreams.remove(s)

                # prevent repeated notifs
                postedStreams = await self.bot.db.escape(json.dumps(postedStreams, cls=SetEncoder))
                await self.bot.db.execute(
                    f"INSERT INTO twitching.twitch (guildID, postedStreamIDs) "
                    f"VALUES ('{guild.id}', '{postedStreams}') ON DUPLICATE KEY UPDATE "
                    f"postedStreamIDs = '{postedStreams}'"
                )
        except Exception as ex:
            log.error('Ignoring exception in twitch: {}'.format(
                "".join(traceback.format_exception(type(ex), ex,
                                                   ex.__traceback__))))

    @cog_ext.cog_subcommand(base="twitch", subcommand_group="channel", name="set",
                            description="Set the channel to post live notifications to",
                            options=[
                                manage_commands.create_option(
                                    name="channel",
                                    description="The channel you want updates in",
                                    option_type=7,
                                    required=True
                                )
                            ],
                            )
    async def setChannel(self, ctx: SlashContext,
                         channel: typing.Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel]):
        await ctx.respond()
        if not self.check_perms(ctx):
            return await ctx.send("Sorry you need manage_messages to use this command", hidden=True)

        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("I can only post in a Text Channel")

        me: discord.Member = ctx.guild.get_member(self.bot.user.id)
        perms: discord.Permissions = channel.permissions_for(me)

        if not perms.administrator:
            if not perms.send_messages or \
                    not perms.read_messages or \
                    not perms.embed_links or \
                    not perms.read_message_history:
                embed = discord.Embed(title=f"Missing permissions in {channel.name}",
                                      colour=discord.Colour.red())
                embed.description = "Sorry I am missing perms in that channel"
                embed.add_field(
                    name="Send Messages", value="✅" if perms.send_messages else "❌",
                )
                embed.add_field(
                    name="Read Messages", value="✅" if perms.read_messages else "❌"
                )
                embed.add_field(
                    name="Embed Links", value="✅" if perms.embed_links else "❌"
                )
                embed.add_field(
                    name="Read Message History", value="✅" if perms.read_message_history else "❌"
                )
                return await ctx.send(embed=embed)

        await self.bot.db.execute(
            f"INSERT INTO twitching.twitch (guildID, postChannel) "
            f"VALUES ('{ctx.guild_id}', '{channel.id}') "
            f"ON DUPLICATE KEY UPDATE postChannel = '{channel.id}'"
        )

        embed = discord.Embed(title=f"Posting notifications in {channel.name}",
                              colour=discord.Colour.blurple())
        await ctx.send(embed=embed)

    @cog_ext.cog_subcommand(base="twitch", subcommand_group="channel", name="clear",
                            description="Stop posting updates",
                            )
    async def clearChannel(self, ctx: SlashContext):
        await ctx.respond()
        if not self.check_perms(ctx):
            return await ctx.send("Sorry you need manage_messages to use this command", hidden=True)

        await self.bot.db.execute(
            f"INSERT INTO twitching.twitch (guildID, postChannel) "
            f"VALUES ('{ctx.guild_id}', NULL) "
            f"ON DUPLICATE KEY UPDATE postChannel = NULL"
        )

        embed = discord.Embed(title=f"Stopped twitch updates",
                              colour=discord.Colour.blurple())
        await ctx.send(embed=embed)

    @cog_ext.cog_subcommand(base="twitch", subcommand_group="streamer", name="add",
                            description="Add a streamer to the watched list",
                            options=[
                                manage_commands.create_option(
                                    name="name",
                                    description="The streamers login name",
                                    option_type=3,
                                    required=True
                                )
                            ])
    async def streamerAdd(self, ctx: SlashContext, streamer: str):
        await ctx.respond()
        if not self.check_perms(ctx):
            return await ctx.send("Sorry you need manage_messages to use this command", hidden=True)

        streamerName = streamer.lower()  # in case they used displayName
        embed = discord.Embed(title="Adding Streamer", colour=discord.Colour.orange())

        # try and find streamer
        data = await self.bot.loop.run_in_executor(
            self.executor,
            functools.partial(
                self.twitch.get_users,
                logins=streamerName))
        streamer = data['data'][0] if data['data'] else None
        if not streamer:
            embed.colour = discord.Colour.red()
            embed.title = f"Could not find streamer called \"{streamerName}\""
            return await ctx.send(embed=embed)

        # tell user who they're adding
        embed.title = f"Adding {streamer['display_name']} to watch list"
        embed.description = f"*{streamer['description']}*" if streamer['description'] != "" else "No description"
        embed.set_thumbnail(url=streamer['profile_image_url'])
        embed.url = f"https://twitch.tv/{streamer['login']}"
        msg = await ctx.send(embed=embed)

        # write to db
        dbData = await self.bot.db.execute(
            f"SELECT * FROM twitching.twitch WHERE guildID = '{ctx.guild_id}'",
            getOne=True
        )
        try:
            existingStreamers = set() if not dbData else set(json.loads(dbData['twitchChannel']))
        except Exception as e:
            log.error(e)
            existingStreamers = set()

        existingStreamers.add(streamer['id'])

        existingStreamers = await self.bot.db.escape(json.dumps(existingStreamers, cls=SetEncoder))
        await self.bot.db.execute(
            f"INSERT INTO twitching.twitch (guildID, postChannel, twitchChannel) "
            f"VALUES ('{ctx.guild_id}', NULL, '{existingStreamers}') "
            f"ON DUPLICATE KEY UPDATE twitchChannel = '{existingStreamers}'"
        )
        embed.title = f"Added {streamer['display_name']} to watch list"
        embed.colour = discord.Colour.blurple()
        await msg.edit(embed=embed)

    @cog_ext.cog_subcommand(base="twitch", subcommand_group="streamer", name="remove",
                            description="Remove a streamer from the watched list",
                            options=[
                                manage_commands.create_option(
                                    name="name",
                                    description="The streamers login name",
                                    option_type=3,
                                    required=True
                                )
                            ])
    async def streamerRemove(self, ctx: SlashContext, streamer: str):
        await ctx.respond()
        if not self.check_perms(ctx):
            return await ctx.send("Sorry you need manage_messages to use this command", hidden=True)

        streamerName = streamer.lower()  # in case they used displayName
        embed = discord.Embed(title="Removing Streamer", colour=discord.Colour.orange())

        # try and find streamer
        data = await self.bot.loop.run_in_executor(
            self.executor,
            functools.partial(
                self.twitch.get_users,
                logins=streamerName))
        streamer = data['data'][0] if data['data'] else None
        if not streamer:
            embed.colour = discord.Colour.red()
            embed.title = f"Could not find streamer called \"{streamerName}\""
            return await ctx.send(embed=embed)

        # tell user who they're removing
        embed.title = f"Removing {streamer['display_name']} from watch list"
        embed.description = f"*{streamer['description']}*"
        embed.set_thumbnail(url=streamer['profile_image_url'])
        embed.url = f"https://twitch.tv/{streamer['login']}"
        msg = await ctx.send(embed=embed)

        # write to db
        dbData = await self.bot.db.execute(
            f"SELECT * FROM twitching.twitch WHERE guildID = '{ctx.guild_id}'",
            getOne=True
        )
        try:
            existingStreamers = set() if not dbData else set(json.loads(dbData['twitchChannel']))
        except Exception as e:
            log.error(e)
            existingStreamers = set()
        existingStreamers.remove(streamer['id'])

        existingStreamers = await self.bot.db.escape(json.dumps(existingStreamers, cls=SetEncoder))
        await self.bot.db.execute(
            f"INSERT INTO twitching.twitch (guildID, postChannel, twitchChannel) "
            f"VALUES ('{ctx.guild_id}', NULL, '{existingStreamers}') "
            f"ON DUPLICATE KEY UPDATE twitchChannel = '{existingStreamers}'"
        )
        embed.title = f"Removed {streamer['display_name']} from watch list"
        embed.colour = discord.Colour.blurple()
        await msg.edit(embed=embed)

    @cog_ext.cog_subcommand(base="twitch", subcommand_group="streamer", name="list",
                            description="Posts an embed per streamer you have tracked. Good for stream links channels")
    async def twitchLinks(self, ctx):
        await ctx.respond()
        if not self.check_perms(ctx):
            return await ctx.send("Sorry you need manage_messages to use this command", hidden=True)
        try:
            embeds = []
            data = await self.bot.db.execute(
                f"SELECT twitchChannel FROM twitching.twitch WHERE guildID = '{ctx.guild_id}'",
                getOne=True
            )
            streamers = json.loads(data['twitchChannel'])
            if streamers is not None:
                streamerData = await self.bot.loop.run_in_executor(
                    self.executor,
                    functools.partial(
                        self.twitch.get_users,
                        user_ids=streamers))
                streamerData = streamerData
                streamerData = sorted(streamerData['data'], key=lambda k: k['login'])
                for sData in streamerData:
                    embed = discord.Embed(title=sData['display_name'])
                    embed.colour = await utilities.getDominantColour(self.bot, sData['profile_image_url'])
                    embed.description = sData['description']
                    embed.set_image(url=sData['profile_image_url'])
                    embed.url = f"https://twitch.tv/{sData['login']}"
                    embeds.append(embed)
                await ctx.send(embeds=embeds)
        except Exception as e:
            log.error(e)

    @cog_ext.cog_subcommand(base="twitch", subcommand_group="streamer", name="mention",
                            description="Mention a role when a stream goes live",
                            options=[
                                manage_commands.create_option(
                                    name="streamer",
                                    description="Only notify for a specific streamer",
                                    option_type=str,
                                    required=False
                                ),
                                manage_commands.create_option(
                                    name="role",
                                    description="The role to mention",
                                    option_type=8,
                                    required=True
                                )
                            ])
    async def mention(self, ctx: SlashContext, **kwargs):
        await ctx.respond()

        role: discord.Role = kwargs['role']
        streamerName = kwargs['streamer'].lower() if "streamer" in kwargs else "all"

        if not role.mentionable:
            return await ctx.send(f"`{role.name}` is set to **not** be mentionable in your server settings")

        # check if streamer is real
        sData = await self.bot.loop.run_in_executor(
            self.executor,
            functools.partial(
                self.twitch.get_users,
                logins=streamerName))

        sData = sData['data'][0] if sData['data'] else None
        if sData is None:
            return await ctx.send(f"Sorry I couldn't find a streamer called {streamerName}")

        data = await self.bot.db.escape(json.dumps({sData['user_id']: str(role.id)}))

        await self.bot.db.execute(
            f"INSERT INTO twitching.twitch (guildID, mentions) ('{ctx.guild_id}', '{data}') "
            f"ON DUPLICATE KEY UPDATE mentions = '{data}'"
        )

        await ctx.send("")


def setup(bot):
    """Called when this cog is mounted"""
    bot.add_cog(Twitch(bot))
    log.info("Twitch mounted")


def teardown(bot):
    """Called when this cog is unmounted"""
    log.warning('Twitch un-mounted')
    for handler in log.handlers[:]:
        log.removeHandler(handler)
