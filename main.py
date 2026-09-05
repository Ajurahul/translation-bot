import asyncio
import datetime
import gc
import logging
import os
import httpcore
# Dynamically add the missing type hint variable so googletrans doesn't crash
if not hasattr(httpcore, 'SyncHTTPTransport'):
    httpcore.SyncHTTPTransport = None
import discord
from discord.ext import commands
from discord.ext import tasks

from cogs.admin import Admin
from utils.handler import FileHandler as handler

from core.bot import Raizel

bot = Raizel()
bot.cache_max_messages = 100

_looper_log = logging.getLogger("looper")

# Scheduled restarts fire at most twice a day, at fixed UTC times, instead of
# the old "every 15 loops of a 10-minute interval" counter (~every 2.5 hours,
# and prone to drift/duplication if on_ready fired more than once). Trim this
# list to a single entry for a once-a-day restart.
RESTART_TIMES = [
    datetime.time(hour=4, minute=0, tzinfo=datetime.timezone.utc),
    datetime.time(hour=16, minute=0, tzinfo=datetime.timezone.utc),
]


@tasks.loop(minutes=10)
async def housekeeping():
    """Cheap, frequent upkeep only - no restart logic here anymore."""
    try:
        await handler.update_status(bot)
    except Exception:
        _looper_log.exception("[housekeeping] failed to update status")
    gc.collect()


@housekeeping.before_loop
async def before_housekeeping():
    await bot.wait_until_ready()


@housekeeping.error
async def housekeeping_error(error: BaseException):
    # tasks.loop silently dies forever on an unhandled exception unless we
    # catch and restart it here.
    _looper_log.error("[housekeeping] loop crashed, restarting it", exc_info=error)
    if not housekeeping.is_running():
        housekeeping.restart()


@tasks.loop(minutes=20)
async def idle_cleanup_loop():
    """Frees RAM/disk when the bot is idle - see Raizel.idle_cleanup().
    Runs every 20 min but only actually removes anything on ticks where no
    translate/crawl job is running, so it never interferes with active work."""
    try:
        stats = await bot.idle_cleanup()
        if stats["files_removed"] or stats["chrome_dirs_removed"]:
            _looper_log.info(
                "[idle_cleanup] freed %d scratch file(s) (%.1f KB), %d stale chrome profile dir(s), "
                "gc collected %d objects",
                stats["files_removed"], stats["bytes_freed"] / 1024,
                stats["chrome_dirs_removed"], stats["gc_collected"],
            )
    except Exception:
        _looper_log.exception("[idle_cleanup] failed")


@idle_cleanup_loop.before_loop
async def before_idle_cleanup_loop():
    await bot.wait_until_ready()


@idle_cleanup_loop.error
async def idle_cleanup_loop_error(error: BaseException):
    _looper_log.error("[idle_cleanup] loop crashed, restarting it", exc_info=error)
    if not idle_cleanup_loop.is_running():
        idle_cleanup_loop.restart()


@tasks.loop(time=RESTART_TIMES)
async def scheduled_restart():
    """Runs once per timestamp in RESTART_TIMES, so at most len(RESTART_TIMES)
    times per day - not on every loop tick."""
    try:
        chan = bot.get_channel(
            991911644831678484
        ) or await bot.fetch_channel(991911644831678484)
        msg_new2 = await chan.fetch_message(1052750970557308988)
        context_new2 = await bot.get_context(msg_new2)
        await chan.send("> Scheduled bot restart starting (max "
                         f"{len(RESTART_TIMES)}x/day)")
        await bot.get_command("restart").callback(Admin(bot), context_new2)
    except Exception:
        _looper_log.exception("[scheduled_restart] failed to run scheduled restart")


@scheduled_restart.before_loop
async def before_scheduled_restart():
    await bot.wait_until_ready()


@scheduled_restart.error
async def scheduled_restart_error(error: BaseException):
    _looper_log.error("[scheduled_restart] loop crashed, restarting it", exc_info=error)
    if not scheduled_restart.is_running():
        scheduled_restart.restart()


@bot.event
async def on_ready():
    print(f"Running as {bot.user}")
    await bot.tree.sync()
    # on_ready can fire more than once (e.g. on reconnect) - guard against
    # calling .start() on an already-running loop, which raises RuntimeError.
    if not housekeeping.is_running():
        housekeeping.start()
    if not scheduled_restart.is_running():
        scheduled_restart.start()
    if not idle_cleanup_loop.is_running():
        idle_cleanup_loop.start()


# @bot.event
# async def on_command(ctx: commands.Context):
#     gc.collect()
#     bot.logger.info(
#         f"Command {ctx.command if ctx.command else 'Unknown Command'} called by {ctx.author} in {ctx.channel} with args {ctx.args} and kargs {ctx.kwargs}")


async def main():
    async with bot:
        await bot.start()


if __name__ == "__main__":
    asyncio.run(main())