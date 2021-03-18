import typing

import discord
from discord.ext import commands

from . import databaseManager


class Bot(commands.Bot):
    """Expands on the default bot class, and helps with type-hinting """

    def __init__(self, cogList=list, *args, **kwargs):
        self.cogList = cogList
        """A list of cogs to be mounted"""

        self.db = databaseManager.DBConnector()
        """The bots database"""

        self.appInfo: discord.AppInfo = None
        """A cached application info"""

        self.startTime = None
        """The time the bot started"""

        self.shouldUpdateBL = True
        """Should the bot try and update bot-lists"""

        self.perms = 0
        """The perms the bot needs"""

        super().__init__(*args, **kwargs)

    async def getMessage(self, messageID: int, channel: discord.TextChannel) -> typing.Union[discord.Message, None]:
        """Gets a message using the id given
        we dont use the built in get_message due to poor rate limit
        """
        for message in self.cached_messages:
            if message.id == messageID:
                return message
        # bot has not cached this message, so search the channel for it
        try:
            o = discord.Object(id=messageID + 1)
            msg = await channel.history(limit=1, before=o).next()

            if messageID == msg.id:
                return msg

            return None
        except discord.NoMoreItems:
            # the message could not be found
            return None
        except Exception as e:
            print(e)
        return None
