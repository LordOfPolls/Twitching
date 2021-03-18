import asyncio
import json
import logging
import os
import typing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import time, sleep

import aiomysql
import sshtunnel

from . import utilities

log: logging.Logger = utilities.getLog("database", logging.INFO)

if not os.path.isfile("data/DBLogin.json"):
    log.warning("Database login not present, please input")
    sleep(1)
    serverAddress = input("Server IP - ")
    serverPort = int(input("SSH Port - "))
    localAddress = input("Local Address - ")
    localPort = int(input("Local Port - "))
    sshUser = input("SSH User - ")
    DBUser = input("DB Username - ")
    DBPass = input("DB Password - ")

    data = {"serverAddress": serverAddress,
            "serverPort": serverPort,
            "localAddress": localAddress,
            "localPort": localPort,
            "sshUser": sshUser,
            "dbUser": DBUser,
            "dbPass": DBPass
            }

    f = open("data/DBLogin.json", "w")
    json.dump(data, f)
    f.close()

f = open("data/DBLogin.json", "r")
data = json.load(f)
f.close()
serverAddress = data['serverAddress']
serverPort = data['serverPort']
localAddress = data['localAddress']
localPort = data['localPort']
sshUser = data['sshUser']
DBUser = data['dbUser']
DBPass = data['dbPass']


class DBConnector:
    def __init__(self, loop=asyncio.get_event_loop()):
        self.tunnel = None
        self.loop = loop
        self.dbPool = None
        self.threadPool = ThreadPoolExecutor(max_workers=4)
        self.operations = 0
        self.time = Time

    def teardown(self):
        if self.tunnel:
            self.tunnel.close()
        self.threadPool.shutdown(wait=True)

    async def escape(self, inputString: str):
        """Escape the input"""
        async with self.dbPool.acquire() as conn:
            return conn.escape_string(inputString)

    async def _connect(self):
        """Creates a connection to the database, either directly or through a tunnel"""
        log.spam("Attempting to connect to local database")
        try:
            self.dbPool = await aiomysql.create_pool(
                user=DBUser,
                password=DBPass,
                host="127.0.0.1",
                port=3306,
                auth_plugin="mysql_native_password",
                maxsize=10
            )
        except:
            # Probably working on a dev machine, create a tunnel
            log.warning("Unable to connect to database, attempting to create SSH Tunnel")
            self.tunnel = sshtunnel.open_tunnel((serverAddress, serverPort),
                                                ssh_username=sshUser,
                                                ssh_pkey="opensshkey.ppk",
                                                remote_bind_address=(localAddress, localPort),
                                                local_bind_address=(localAddress, localPort),
                                                logger=utilities.getLog("tunnel", logging.CRITICAL))
            self.tunnel.start()
            while not self.tunnel.is_active:
                # Wait for the tunnel to be considered active
                time.sleep(0.1)
            log.spam(
                f"Connected to DB Server: {self.tunnel.is_active}. "
                f"LocalAddr: {self.tunnel.local_bind_host}:{self.tunnel.local_bind_port}")
            log.debug("Attempting to connect to tunneled database")
            try:
                self.dbPool = await aiomysql.create_pool(
                    user=DBUser,
                    password=DBPass,
                    host=self.tunnel.local_bind_host,
                    port=self.tunnel.local_bind_port,
                    auth_plugin="mysql_native_password",
                    maxsize=10,
                )
            except Exception as e:
                log.critical(f"Failed to connect to db, aborting startup: {e}")
                exit(1)

        # Configure db to accept emoji inputs (i wish users didnt do this, but i cant stop em)
        await self.execute('SET NAMES utf8mb4;')
        await self.execute('SET CHARACTER SET utf8mb4;')
        await self.execute('SET character_set_connection=utf8mb4;')

        databases = await self.execute("SHOW SCHEMAS")
        log.info(f"Database connection established. {len(databases)} schemas found")
        return True

    async def execute(self, query: str, getOne: bool = False) -> typing.Union[dict, None]:
        """
        Execute a database query
        :param query: The query you want to make
        :param getOne: If you only want one item, set this to True
        :return: a dict representing the mysql result, or None
        """

        try:
            # make sure we have a connection first
            async with self.dbPool.acquire() as conn:
                await conn.ping(reconnect=True)  # ping the database, to make sure we have a connection
        except Exception as e:
            log.error(f"{e}")
            await asyncio.sleep(5)  # sleep for a few seconds
            await self.connect()  # Attempt to reconnect

        try:
            log.debug(f"Executing Query - {query}")

            async with self.dbPool.acquire() as connection:
                async with connection.cursor(aiomysql.SSDictCursor) as cursor:
                    await cursor.execute(query)  # execute the query
                    if not getOne:
                        result = await cursor.fetchall()
                    else:
                        result = await cursor.fetchone()
                    if isinstance(result, tuple):
                        if len(result) == 0:
                            return None
                    await cursor.close()
                await connection.commit()
            self.operations += 1
            return result
        except Exception as e:
            log.error(e)
            if "cannot connect" in str(e):
                await asyncio.sleep(5)
                await self.execute(query=query, getOne=getOne)
        return None

    async def connect(self):
        """Public function to connect to the database"""
        await self._connect()


class Time:
    """\
*Convenience class for easy format conversion*
Accepts time() float, datetime object, or SQL datetime str.
If no time arg is provided, object is initialized with time().
id kwarg can be used to keep track of objects.
Access formats as instance.t, instance.dt, or instance.sql.

https://stackoverflow.com/a/59906601
    """

    f = '%Y-%m-%d %H:%M:%S'

    def __init__(self, *arg, id=None) -> None:
        self.id = id
        if len(arg) == 0:
            self.t = time()
            self.dt = self._dt
            self.sql = self._sql
        else:
            arg = arg[0]
            if isinstance(arg, float) or arg == None:
                if isinstance(arg, float):
                    self.t = arg
                else:
                    self.t = time()
                self.dt = self._dt
                self.sql = self._sql
            elif isinstance(arg, datetime):
                self.t = arg.timestamp()
                self.dt = arg
                self.sql = self._sql
            elif isinstance(arg, str):
                self.sql = arg
                if '.' not in arg:
                    self.dt = datetime.strptime(self.sql, Time.f)
                else:
                    normal, fract = arg.split('.')
                    py_t = datetime.strptime(normal, Time.f)
                    self.dt = py_t.replace(
                        microsecond=int(fract.ljust(6, '0')[:6]))
                self.t = self.dt.timestamp()

    @property
    def _dt(self) -> datetime:
        return datetime.fromtimestamp(self.t)

    @property
    def _sql(self) -> str:
        t = self.dt
        std = t.strftime(Time.f)
        fract = f'.{str(round(t.microsecond, -3))[:3]}'
        return std + fract

    def __str__(self) -> str:
        if self.id == None:
            return self.sql
        else:
            return f'Time obj "{self.id}": {self.sql}'
