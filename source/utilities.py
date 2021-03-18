import base64
import binascii
import logging
import os
import pickle
from concurrent.futures.thread import ThreadPoolExecutor

import aiofiles as aiofiles
import aiohttp
import colorlog
import numpy as np
import scipy.cluster
from PIL import Image
from colorlog import ColoredFormatter

import source.pagination as pagination

paginator = pagination

thread_pool = ThreadPoolExecutor(max_workers=2)  # a thread pool

discordCharLimit = 2000

logging.SPAM = 9
logging.addLevelName(logging.SPAM, "SPAM")


def spam(self, message, *args, **kws):
    self._log(logging.SPAM, message, args, **kws)


logging.Logger.spam = spam


def getLog(filename, level=logging.DEBUG) -> logging:
    """ Sets up logging, to be imported by other files """
    streamHandler = colorlog.StreamHandler()
    streamFormatter = ColoredFormatter(
        "{asctime} {log_color}|| {levelname:^8} || {name:^11s} || {reset}{message}",
        datefmt="%H:%M:%S",
        reset=True,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_yellow',
            'SPAM': 'purple'
        },
        secondary_log_colors={},
        style='{'
    )

    streamHandler.setLevel(level)
    streamHandler.setFormatter(streamFormatter)

    _log = colorlog.getLogger(filename)

    _log.addHandler(streamHandler)
    _log.setLevel(logging.DEBUG)
    return _log


log = getLog("utils")


def getCredential(name: str):
    """Retrieves a stored credential

    if one doesnt exist, make the user set it"""
    try:
        # try and retrieve stored token
        file = open(f"data/{name}.pkl", "rb")
        credential = pickle.load(file)
        credential = base64.b64decode(credential)
    except:
        # get user to store token
        file = open(f"data/{name}.pkl", "wb")
        credential = input(f"{name} has not been stored before. Please enter it: ").strip().encode('utf-8')
        credential = base64.b64encode(credential)
        pickle.dump(credential, file)
    file.close()
    credential = credential.decode('utf-8')
    return credential


async def getDominantColour(bot, imageURL):
    """Returns the dominant colour of an image from URL"""

    def blockFunc(imageDir):
        """This is the actual MEAT that gets the dominant colour,
        it is fairly computationally intensive, so i spin up a new thread
        to avoid blocking the main bot thread"""
        # log.debug("Reading image...")
        im = Image.open(imageDir)

        im = im.resize((100, 100), Image.NEAREST)

        ar = np.asarray(im)
        shape = ar.shape
        ar = ar.reshape(np.product(shape[:2]), shape[2]).astype(float)

        # log.debug("Finding Clusters")
        codes, dist = scipy.cluster.vq.kmeans(ar, 5)

        # log.debug("Clusters found")
        vecs, dist = scipy.cluster.vq.vq(ar, codes)  # assign codes
        counts, bins = np.histogram(vecs, len(codes))  # count occurrences

        # log.debug("Processing output")

        maxSorted = counts.argsort().tolist()
        maxSorted.reverse()
        for i in range(len(maxSorted)):
            peak = codes[maxSorted[i]]
            c = binascii.hexlify(bytearray(int(c) for c in peak)).decode('ascii')
            if c != '00000000' and c != '00000001':
                return c

    async with aiohttp.ClientSession() as session:
        async with session.get(imageURL) as r:
            # Asynchronously get image from url
            if r.status == 200:
                name = imageURL.split("/")[-1]
                f = await aiofiles.open(name, mode="wb")
                await f.write(await r.read())
                await f.close()

                loop = bot.loop
                colour = await loop.run_in_executor(thread_pool, blockFunc, name)
                os.unlink(name)
                colour = tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))
                colour = (colour[0] << 16) + (colour[1] << 8) + colour[2]
                return colour
    return None
