import asyncio
import datetime
import gc
import glob
import json
import logging
import os
import pickle
import random
import time
import traceback
import typing as t
from asyncio import Task
from logging.handlers import RotatingFileHandler

import aiohttp
import discord
import joblib
import nltk
from discord.ext import commands
from mega import Mega

from languages.languages import choices
from languages.sites import sites
from languages.terms import get_dictionary
from utils.connector import Mongo


class Raizel(commands.Bot):
    con: aiohttp.ClientSession
    boot: datetime.datetime
    allowed: list[str]
    mongo: Mongo
    mega: Mega = None

    def __init__(self) -> None:
        self.log_path = None
        self.blocked = None
        self.logger = None
        # Set up logging immediately so it's available to all cogs from the start
        self.logger = self.setup_logging()
        intents = discord.Intents.all()
        intents.members = True
        intents.message_content = True
        intents.typing = False
        intents.presences = False
        self.translator: t.Dict[int, str] = {}
        self.crawler: t.Dict[int, str] = {}
        self.crawler_next: t.Dict[int, str] = {}
        self.chrome = 0
        self.translator_tasks: t.Dict[int, Task] = {}
        self.crawler_tasks: t.Dict[int, Task] = {}
        self.languages = choices
        self.dictionary: list[str] = None
        self.boot = datetime.datetime.utcnow()
        self.app_status: str = "up"
        self.update: bool = False
        self.translation_count: float = 0
        self.crawler_count = 0
        self.cache_max_messages = 100
        self._heartbeat_task: Task | None = None
        self.healthcheck_path = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs', 'healthcheck.json')
        )
        super().__init__(
            command_prefix=commands.when_mentioned_or(".t"),
            intents=intents,
            strip_after_prefix=True,
            case_insensitive=True,
            help_command=None,
        )

    async def _load_cogs(self, reload_if_loaded=False) -> None:
        if not reload_if_loaded:
            for extension in os.listdir("cogs"):
                if extension.endswith(".py") and extension[:2] != "__":
                    await self.load_extension(f"cogs.{extension[:-3]}")
                    print(f"Loaded {extension}")
            return
        for extension in os.listdir("cogs"):
            if extension.endswith(".py") and extension[:2] != "__":
                try:
                    await self.load_extension(f"cogs.{extension[:-3]}")
                except commands.ExtensionAlreadyLoaded:
                    await self.reload_extension(f"cogs.{extension[:-3]}")

    async def setup_hook(self) -> None:
        try:
            await self._load_cogs()
            await self.load_extension("jishaku")
        except Exception as e:
            print(traceback.print_exc())
            print("cogs already loaded")
        self.allowed = sites
        # Logger is already set up in __init__; re-apply log_path here in case setup_hook runs fresh
        if self.logger is None:
            self.logger = self.setup_logging()
        await self.write_healthcheck(status="starting")
        self._heartbeat_task = asyncio.create_task(self.heartbeat_loop())
        self.con = aiohttp.ClientSession()
        self.mongo = Mongo()
        self.logger.info("Connected to mongo db")
        channel = await self.fetch_channel(991911644831678484)
        await channel.send(embed=discord.Embed(description=f"Bot is up now"))
        txt_channel = await self.fetch_channel(984664133570031666)
        await txt_channel.send(embed=discord.Embed(description=f"Bot is up now"))
        asyncio.create_task(self.startup(channel=channel))
        self.logger.info("Bot is up now")
        try:
            await self.tree.sync()
        except Exception as e:
            self.logger.error(f"Failed to sync command tree: {e}")
        return await super().setup_hook()

    async def write_healthcheck(self, status: str = "running"):
        os.makedirs(os.path.dirname(self.healthcheck_path), exist_ok=True)
        payload = {
            "status": status,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "pid": os.getpid(),
            "uptime_seconds": int((datetime.datetime.utcnow() - self.boot).total_seconds()),
            "app_status": self.app_status,
            "closed": self.is_closed(),
        }
        temp_path = f"{self.healthcheck_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp)
        os.replace(temp_path, self.healthcheck_path)

    async def heartbeat_loop(self):
        while not self.is_closed():
            try:
                await self.write_healthcheck(status="running")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"failed to write healthcheck: {e}")
            await asyncio.sleep(30)

    async def startup(self, channel):
        try:
            await self.tree.sync()
        except:
            pass
        nltk.download("brown")
        nltk.download("punkt")
        nltk.download("popular")
        self.blocked: list[int] = await self.mongo.blocker.get_all_banned_users()
        self.dictionary = get_dictionary()
        for x in os.listdir():
            if x.endswith("txt") and "requirements" not in x:
                await channel.send(f"deleting {x}")
                print(f"deleting {x}")
                os.remove(x)
        await self.connect_mega_account(channel)
        await self.load_title()
        n = await self.add_roles()
        if n > 0:
            await channel.send(f"Added Storage access to {n} users")

    async def connect_mega_account(self, channel):
        """Connect to Mega without blocking the event loop, retrying
        transient failures, and logging the *real* exception instead of
        swallowing it.

        The old version called `Mega().login(...)` directly (a blocking,
        synchronous `requests` call) with no `await`/executor, which froze
        the entire bot - every guild, every command - for as long as that
        HTTP call took. It also had two bugs in its error handling: the
        fallback branch's own exception was caught by a bare `except:` and
        discarded, so the message you saw always reported the *first*
        failure (the login with stored credentials), never the second one
        (the anonymous login). That's why the reported error
        (`_EventBundle.__init__() takes 1 positional argument but 2 were
        given`) looked disconnected from "Mega" - it's a low-level HTTP
        client error being raised from inside `requests`/its dependency
        chain during that first login attempt, not anything about your
        Mega credentials. It's consistent with a version mismatch between
        `httpx`/`httpcore`/`h11` in requirements.txt (see the
        `SyncHTTPTransport` compatibility shim in main.py, which is a sign
        this dependency chain has caused problems before). Now that the
        full traceback is logged via `self.logger.exception`, the next
        occurrence will show exactly which line raised it.
        """
        megastore = None
        try:
            with open(os.getenv("MEGA"), 'rb') as f:
                megastore = pickle.load(f)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[mega] could not read stored credentials: {e}")

        def _login_with_credentials():
            return Mega().login(megastore["user"], megastore["password"])

        def _login_anonymous():
            return Mega().login()

        last_error: Exception | None = None
        if megastore:
            for attempt in range(1, 4):
                try:
                    self.mega = await self.loop.run_in_executor(None, _login_with_credentials)
                    print("Connected to Mega")
                    return
                except Exception as e:
                    last_error = e
                    if self.logger:
                        self.logger.warning(
                            f"[mega] login attempt {attempt}/3 with stored credentials failed: {e!r}")
                    else:
                        print(f"[mega] login attempt {attempt}/3 failed: {e!r}")
                    if attempt < 3:
                        await asyncio.sleep(attempt * 5)

        # Stored-credential login failed (or there were no stored
        # credentials) - fall back to an anonymous session so
        # upload/download features degrade gracefully instead of leaving
        # self.mega unset.
        try:
            self.mega = await self.loop.run_in_executor(None, _login_anonymous)
            print("mega login with stored credentials failed, connected anonymously")
            if last_error is not None:
                if self.logger:
                    self.logger.warning(f"[mega] falling back to anonymous session: {last_error!r}")
                await channel.send(
                    f"> <@&1020638168237740042> **Couldn't connect to Mega with stored credentials, "
                    f"connected anonymously instead.**\n```{last_error}```",
                    allowed_mentions=discord.AllowedMentions(roles=False))
        except Exception as anon_error:
            self.mega = None
            if self.logger:
                self.logger.exception("[mega] anonymous login also failed")
            await channel.send(
                f"> <@&1020638168237740042> **Couldn't connect with Mega servers. "
                f"some problem with connection**\n```{anon_error}```",
                allowed_mentions=discord.AllowedMentions(roles=False))

    def is_busy(self) -> bool:
        """Safe, read-only check for whether any translate/crawl job is in
        flight. Never mutates self.translator/self.crawler/self.crawler_next
        - those dicts double as the live progress store that commands like
        `.t progress` read from (utils/translate.py writes strings like
        "12/50" into them while a job runs), so clearing them mid-job wipes
        that progress out from under a job that's still running and makes
        anything checking "is someone busy" wrongly report "no" a moment
        later, even though the job never stopped.

        Backed by both the progress dicts *and* the task dicts, since a task
        can briefly exist without a progress-dict entry yet (or vice versa)
        around job start/cleanup.
        """
        if self.translator or self.crawler or self.crawler_next:
            return True
        if any(not task.done() for task in self.translator_tasks.values()):
            return True
        if any(not task.done() for task in self.crawler_tasks.values()):
            return True
        return False

    def active_job_user_ids(self) -> list[int]:
        """User IDs with a translate/crawl job currently tracked, for
        status/progress messages. Read-only, see is_busy() above."""
        ids = set(self.translator.keys()) | set(self.crawler.keys()) | set(self.crawler_next.keys())
        ids |= {uid for uid, task in self.translator_tasks.items() if not task.done()}
        ids |= {uid for uid, task in self.crawler_tasks.items() if not task.done()}
        return list(ids)

    def _sweep_orphaned_scratch_files(self, min_age_seconds: float = 900) -> tuple[int, int]:
        """Removes leftover per-user working files (e.g. ``123.txt``,
        ``123_cr.txt``, ``123.docx``, ``123.pdf``) sitting in the working
        directory. These are written by utils/handler.py while a job runs
        and are normally deleted when that job finishes, but any crash or
        unhandled exception on the way there leaves them behind, and until
        now they only got cleaned up at process startup/manual restart -
        meaning they could sit there using disk for days on a
        long-uptime process.

        Only ever called while self.is_busy() is False (see idle_cleanup),
        and additionally only touches files older than ``min_age_seconds``
        as a second safety net against catching a file that's mid-write for
        a job that hasn't been registered in self.translator/self.crawler
        yet. Blocking filesystem calls only - call via run_in_executor.
        """
        removed, freed_bytes = 0, 0
        now = time.time()
        patterns = ("*.txt", "*.docx", "*.pdf", "*.epub")
        skip = {"requirements.txt"}
        for pattern in patterns:
            for path in glob.glob(pattern):
                name = os.path.basename(path)
                if name in skip or "requirements" in name:
                    continue
                try:
                    stat = os.stat(path)
                    if now - stat.st_mtime < min_age_seconds:
                        continue
                    size = stat.st_size
                    os.remove(path)
                    removed += 1
                    freed_bytes += size
                except OSError:
                    continue
        return removed, freed_bytes

    def _sweep_stale_chrome_profiles(self, min_age_seconds: float = 1800) -> int:
        """Removes leftover Chrome/chromedriver temp profile directories
        under /tmp. Selenium (cogs/crawler.py's get_driver()) doesn't pass
        an explicit --user-data-dir, so Chrome creates a fresh scratch
        profile per run and normally deletes it on driver.quit() - but a
        crash, timeout, or killed process leaves it behind. Only runs while
        self.chrome == 0 (no headless crawl currently using Chrome) so it
        can never touch a live profile. Blocking - call via run_in_executor.
        """
        if self.chrome != 0:
            return 0
        removed = 0
        now = time.time()
        for pattern in (".com.google.Chrome.*", ".org.chromium.Chromium.*", "scoped_dir*"):
            for path in glob.glob(os.path.join("/tmp", pattern)):
                try:
                    if now - os.path.getmtime(path) < min_age_seconds:
                        continue
                    if os.path.isdir(path):
                        import shutil
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        os.remove(path)
                    removed += 1
                except OSError:
                    continue
        return removed

    async def idle_cleanup(self) -> dict:
        """Frees RAM/disk while the bot is idle. Safe to call on a timer -
        it's a no-op beyond gc.collect() unless self.is_busy() is False, so
        it never touches a running job's files or state."""
        stats = {"gc_collected": 0, "files_removed": 0, "bytes_freed": 0, "chrome_dirs_removed": 0}
        stats["gc_collected"] = gc.collect()
        if not self.is_busy():
            removed, freed = await self.loop.run_in_executor(None, self._sweep_orphaned_scratch_files)
            stats["files_removed"] = removed
            stats["bytes_freed"] = freed
            stats["chrome_dirs_removed"] = await self.loop.run_in_executor(
                None, self._sweep_stale_chrome_profiles)
        return stats

    async def add_roles(self) -> int:
        guild = await self.fetch_guild(940866934214373376)
        role = guild.get_role(1076124121592770590)
        top = await self.mongo.library.get_user_novel_count(_top_200=True)
        top_200 = [(user_id, count) for user_id, count in top.items()]
        chunks = [top_200[i: i + 10] for i in range(0, len(top_200), 10)]
        user_ids = []
        no = 0
        for chunk in chunks:
            for user_id, count in chunk:
                if count >= 20:
                    user_ids.append(user_id)
        members = [member async for member in guild.fetch_members()]
        banned_members = await self.mongo.blocker.get_all_banned_users()
        for member in members:
            if member.id in user_ids and role not in member.roles and member.id not in banned_members:
                no = no + 1
                print(f"adding role to {member.name}")
                await member.add_roles(role)
        return no

    async def load_title(self):
        print("started loading titles")
        try:
            titles = list(dict.fromkeys(list(await self.mongo.library.get_all_distinct_titles)))
            titles = random.sample(titles, len(titles))
            joblib.dump(titles, 'titles.sav')
            print("Loaded titles")
            del titles
        except Exception as e:
            print("error loading titles")
            print(e)

    def setup_logging(self):
        self.log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs', 'bot.txt')
        self.log_path = os.path.normpath(self.log_path)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        _logger = logging.getLogger("raizel_bot")
        _logger.setLevel(logging.DEBUG)
        _logger.propagate = False
        # Remove existing handlers to avoid duplicates on re-setup
        _logger.handlers.clear()
        # File handler (rotating, 10MB max, keep 5 backups)
        loghandler = RotatingFileHandler(encoding="utf-8", filename=self.log_path, maxBytes=10 * 1024 * 1024, backupCount=5)
        loghandler.setFormatter(formatter)
        loghandler.setLevel(logging.DEBUG)
        _logger.addHandler(loghandler)
        # Stream handler so errors are visible in console/systemd journal
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(logging.INFO)
        _logger.addHandler(stream_handler)
        return _logger

    async def start(self) -> None:
        try:
            return await super().start(os.getenv("TOKEN"), reconnect=True)
        except Exception as e:
            try:
                await super().close()
            except:
                pass
            print('error occurred on connecting to Discord client... will try after 60 secs')
            print(e)

    async def close(self) -> None:
        try:
            await self.write_healthcheck(status="stopping")
        except Exception:
            pass
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        return await super().close()

    @property
    def uptime(self) -> datetime.timedelta:
        return datetime.datetime.now() - self.boot

    @property
    def invite_url(self) -> str:
        return f"https://discord.com/api/oauth2/authorize?client_id={self.user.id}&permissions=8&scope=bot%20applications.commands"

    @property
    def display_langs(self) -> str:
        string = ["{0: ^17}".format(f"{k} --> {v}") for k, v in self.languages.items()]
        string = "\n".join(["".join(string[i: i + 3]) for i in range(0, len(string), 3)])
        return string

    @property
    def all_langs(self) -> list[str]:
        langs = list(self.languages.keys()) + list(self.languages.values())
        return langs

    # @tasks.loop(hours=2)
    # async def auto_restart(self):
    #     # if self.auto_restart.current_loop == 0:
    #         asyncio.create_task(self.load_title())
    # i = 0
    # if self.auto_restart.current_loop != 0:
    #     await self.change_presence(
    #         activity=discord.Activity(
    #             type=discord.ActivityType.watching,
    #             name="for Restart. Please don't start any other tasks till I turn idle",
    #         ),
    #         status=discord.Status.do_not_disturb,
    #     )
    #     self.app_status = "restart"
    #     self.translator = {}
    #     self.crawler = {}
    #     await asyncio.sleep(50)
    #     while True:
    #         print("Started restart")
    #         if (not self.crawler.items() and not self.translator.items()) or i == 20:
    #             print("restart " + str(datetime.datetime.now()))
    #             try:
    #                 for x in os.listdir():
    #                     if x.endswith("txt") and "requirements" not in x:
    #                         print(f"deleting {x}")
    #                         os.remove(x)
    #                     if "titles.sav" in x:
    #                         os.remove(x)
    #             except Exception as e:
    #                 print("exception occurred  in deleting")
    #                 await channel.send(f"error occurred in deleting {x} {e}")
    #             channel = self.get_channel(
    #                 991911644831678484
    #             ) or await self.bot.fetch_channel(991911644831678484)
    #             try:
    #                 await channel.send(embed=discord.Embed(
    #                     description=f"Bot has been auto-restarted. \nBot has "
    #                                 f"translated {str(int(self.translation_count*3.1))}MB novels and"
    #                                 f" crawled {str(self.crawler_count)} novels"
    #                     , colour=discord.Colour.brand_green()))
    #                 del self.titles
    #                 gc.collect()
    #             except:
    #                 pass
    #             try:
    #                 await self.close()
    #                 raise Exception
    #                 # new_ch = self.get_channel(
    #                 #     991911644831678484
    #                 # ) or await self.bot.fetch_channel(991911644831678484)
    #                 # msg_new = await new_ch.fetch_message(1050579735840817202)
    #                 # context_new = await self.bot.get_context(msg_new)
    #                 # command = await self.get_command("restart").callback(Admin(self), context_new)
    #             except Exception as e:
    #                 await self.close()
    #                 raise Exception("closed session")
    #                 print("error occurred at restarting")
    #                 print(e)
    #             break
    #         else:
    #             i = i + 1
    #             print("there are tasks waiting....")
    #             channel = self.get_channel(
    #                 991911644831678484
    #             ) or await self.bot.fetch_channel(991911644831678484)
    #             await channel.send(embed=discord.Embed(
    #                 description="Task is already running.. waiting for it to finish for restart",
    #                 colour=discord.Colour.random()))
    #             self.bot.translator = {}
    #             self.bot.crawler = {}
    #             await asyncio.sleep(60)