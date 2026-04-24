import asyncio
import datetime
import gc
import os
import pickle
import platform
import random
import socket
import subprocess
import sys

import aiofiles
import discord
from discord.ext import commands
from mega import Mega

from cogs.library import Library
from core import Raizel
from core.views.linkview import LinkView
from databases.blocked import User
from databases.data import Novel
from utils.category import Categories
from utils.hints import Hints


def days_hours_minutes(td):
    return td.days, td.seconds // 3600, (td.seconds // 60) % 60


class Admin(commands.Cog):
    def __init__(self, bot: Raizel) -> None:
        self.bot = bot

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="update source code in next restart")
    async def update(self, ctx: commands.Context):
        self.bot.logger.info("Called Update admin function")
        await ctx.defer()
        self.bot.update = True
        await ctx.send(content=f"> Bots source code will be updated in next restart. ")
        return

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="ban user.. Admin only command")
    async def ban(self, ctx: commands.Context, id: str,
                  reason: str = "continuous use of improper names in novel name translation"):
        await ctx.defer()

        if '#' in id:
            name_spl = id.split('#')
            name = name_spl[0]
            discriminator = name_spl[1]
            user = discord.utils.get(self.bot.get_all_members(), name=name, discriminator=discriminator)
            id = user.id
        id = int(id)
        user_data = [
            id,
            reason,
            datetime.datetime.utcnow().timestamp(),
        ]
        user = User(*user_data)
        m_user = self.bot.get_user(id)
        self.bot.logger.info("banning user with id " + id.__str__() + "name : " + m_user.mention.__str__())
        await self.bot.mongo.blocker.ban(user)
        self.bot.blocked = await self.bot.mongo.blocker.get_all_banned_users()
        try:
            await m_user.send(embed=discord.Embed(
                title="Blocked",
                description=f" You have been blocked by admins of @JARVIS bot due to {reason}\n",
                color=discord.Color.red(),
            ))
        except:
            pass
        await ctx.send(
            f"User {m_user.mention} banned due to {reason}"
        )
        channel = self.bot.get_channel(
            991911644831678484
        ) or await self.bot.fetch_channel(991911644831678484)
        try:
            return await channel.send(embed=discord.Embed(
                description=f"User {m_user.name} has been banned by {ctx.author.name}",
                colour=discord.Color.dark_gold())
            )
        except:
            pass

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="Gives all the banned users.. Admin only command")
    async def banned(self, ctx: commands.Context):
        await ctx.send("banned users")
        await self.bot.mongo.blocker.get_all_banned_users()
        self.bot.blocked = await self.bot.mongo.blocker.get_all_banned_users()
        out = '\n'
        for block in self.bot.blocked:
            try:
                print(block)
                users: discord.User = self.bot.get_user(block)
                out = out + users.name + "#" + users.discriminator + "\t"
            except Exception as e:
                print(e)
        return await ctx.send("IDS : " + str(self.bot.blocked) + out)

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="Unban user.. Admin only command")
    async def unban(self, ctx: commands.Context, id: str):
        await ctx.defer()
        if '#' in id:
            name_spl = id.split('#')
            name = name_spl[0]
            discriminator = name_spl[1]
            user = discord.utils.get(self.bot.get_all_members(), name=name, discriminator=discriminator)
            id = user.id
        id = int(id)
        try:
            user = self.bot.get_user(id)
            await user.send(embed=discord.Embed(
                title="Congrats",
                description=f" You have been unbanned by admins. Please follow the guidelines in future",
                color=discord.Color.blurple(),
            ))
        except:
            pass
        await self.bot.mongo.blocker.unban(id)
        self.bot.blocked = await self.bot.mongo.blocker.get_all_banned_users()
        await ctx.send(
            f"Unbanned user : {user.mention}"
        )
        channel = self.bot.get_channel(
            991911644831678484
        ) or await self.bot.fetch_channel(991911644831678484)
        self.bot.logger.info("banning user with id " + id.__str__() + "user :" + user.name)
        try:
            return await channel.send(embed=discord.Embed(
                description=f"User {user.name} has been unbanned by {ctx.author.name}",
                colour=discord.Color.dark_gold())
            )
        except:
            pass

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="send warning to user..Admin only command")
    async def warn(self, ctx: commands.Context, id: str,
                   reason: str = "continuous use of improper names in novel name translation"):
        await ctx.defer()
        if '#' in id:
            name_spl = id.split('#')
            name = name_spl[0]
            discriminator = name_spl[1]
            user = discord.utils.get(self.bot.get_all_members(), name=name, discriminator=discriminator)
            id = user.id
        id = int(id)
        user = self.bot.get_user(id)
        await user.send(embed=discord.Embed(
            title="WARNING!!!",
            description=f" You have been warned by admins of @JARVIS bot due to {reason}\nIf you continue do so , you will be banned from using bot",
            color=discord.Color.yellow(),
        ))
        return await ctx.reply(content=f"Warning has been sent to {user.mention}")

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="get id of the user if name and discriminator provided. Admin only command")
    async def get_id(self, ctx: commands.Context, library_id: int = None, name: str = None, discriminator: str = None):
        """Get id of a user with library id or username
                       Parameters
                       ----------
                       ctx : commands.Context
                           The interaction
                       library_id :
                           it will return the userid of the user who uploaded the novel with this library id
                       name :
                            return the user id of the user with given name
                       discriminator :
                            discriminator(need to be updated, use library id for now
                       """
        await ctx.defer()
        if library_id:
            userid = await self.bot.mongo.library.get_uploader_by_id(library_id)
            await ctx.send(content=f"{userid}")
            return await ctx.send(
                content=f"> uploader of library id {library_id} is :{self.bot.get_user(userid).mention}")
        elif name:
            if '#' in name:
                name_spl = name.split('#')
                name = name_spl[0]
                discriminator = name_spl[1]
            user = discord.utils.get(self.bot.get_all_members(), name=name, discriminator=discriminator)
            return await ctx.send(content=f"{user.id}", ephemeral=True)
        else:
            return await ctx.reply(content="> please provide library id or user name")

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="Restart the bot incase of bot crash. Ping any BOT-admins to restart bot")
    async def restart(self, ctx: commands.Context, instant: bool = False, server: bool = False,
                      git_update: bool = False):
        try:
            await ctx.defer()
        except:
            pass
        await self.bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.unknown, name="Restarting bot"), status=discord.Status.do_not_disturb)
        msg = await ctx.send("Please wait")
        self.bot.app_status = "restart"
        self.bot.logger.info(f"bot restarted by user : {ctx.author.name}")
        no_of_times = 0
        txt_channel = await self.bot.fetch_channel(984664133570031666)
        channel = self.bot.get_channel(
            991911644831678484
        ) or await self.bot.fetch_channel(991911644831678484)
        try:
            while True:
                no_of_times += 1
                print("Started restart")
                if not instant:
                    await asyncio.sleep(3)
                else:
                    break
                if not self.bot.crawler.items() and not self.bot.translator.items() and not self.bot.crawler_next.items():
                    await asyncio.sleep(5)
                    if not self.bot.crawler.items() and not self.bot.translator.items() and not self.bot.crawler_next.items():
                        print("restart " + str(datetime.datetime.now()))
                    else:
                        continue
                    try:
                        await channel.send(embed=discord.Embed(description=f"Bot has started restarting"))
                        await txt_channel.send(embed=discord.Embed(description=f"Bot has started restarting"))
                    except:
                        pass
                    await asyncio.sleep(15)
                    break
                else:
                    print("waiting")
                    self.bot.app_status = "restart"
                    await channel.send(
                        content=f"> {no_of_times} : Restart waiting for {', '.join(self.bot.get_user(k).global_name for k in self.bot.translator.keys())} {', '.join(self.bot.get_user(k).global_name for k in self.bot.crawler.keys())}")
                    self.bot.translator = {}
                    self.bot.crawler = {}
                    await asyncio.sleep(no_of_times * 10)
                    if no_of_times > 10:
                        self.bot.app_status = "up"
                        if git_update is True:
                            self.bot.update = True
                        await channel.send("Restart failed")
                        await txt_channel.send("Restart failed")
                        return None
        except:
            self.bot.app_status = "up"
        finally:
            self.bot.app_status = "up"
        try:
            await msg.edit(
                content="",
                embed=discord.Embed(
                    description=f"Bot is restarting...",
                    color=discord.Color.random(),
                ),
            )
            await txt_channel.send(embed=discord.Embed(description=f"Bot is restarting..."))
        except:
            pass
        try:
            for x in os.listdir():
                if x.endswith("txt") and "requirements" not in x:
                    await ctx.send(f"deleting {x}")
                    print(f"deleting {x}")
                    os.remove(x)
                if "titles.sav" in x:
                    os.remove(x)
        except Exception as e:
            print("exception occurred  in deleting")
            await ctx.send(f"error occurred in deleting {x} {e}")
        try:
            await channel.send(
                f"Bot has been restarted by user : {ctx.author.name} \nBot has translated {str(int(self.bot.translation_count * 3.1))} MB novels and crawled {str(self.bot.crawler_count)} novels"
            )
            await txt_channel.send(
                f"Bot has been restarted by user : {ctx.author.name} \nBot has translated {str(int(self.bot.translation_count * 3.1))} MB novels and crawled {str(self.bot.crawler_count)} novels"
            )
            gc.collect()
        except:
            pass
        # Determine the correct path to git_update.sh
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_dir = os.path.abspath(os.path.join(script_dir, '..'))
        git_update_script = os.path.join(repo_dir, 'scripts', 'git_update.sh')
        if git_update or self.bot.update:
            try:
                subprocess.call(['sh', git_update_script])
                await ctx.reply(content="> ** source code updated**")
            except Exception as e:
                await channel.send("git update failed")
                await channel.send(str(e)[:1900])
        if random.randint(0, 15) < 12 or server is True:
            try:
                await channel.send("Server restarted")
                subprocess.call(['sh', '/home/ec2-user/translation-bot/scripts/server-restart.sh'])
            except Exception as e:
                await channel.send("Server restart failed")
                await channel.send(e.with_traceback().__str__()[:1900])
        for task in asyncio.all_tasks():
            try:
                task.cancel()
            except:
                pass
        return await self.bot.start()
        # print(sys.argv[0])
        # print(sys.argv)
        # os.execv(sys.executable, ['python'] + sys.argv)
        # os.execv(sys.argv[0], sys.argv)

        # raise Exception("TooManyRequests")
        # h = heroku3.from_key(os.getenv("APIKEY"))
        # app = h.app(os.getenv("APPNAME"))
        # app.restart()

    @commands.guild_only()
    @commands.hybrid_command(help="Give the latency and uptime of the bot... ")
    async def status(self, ctx: commands.Context):
        # await ctx.send(str(datetime.datetime.utcnow())+".-"+str(ctx.message.created_at))
        await ctx.defer()
        embed = discord.Embed(title="Status", description="Status of the bot", color=discord.Color.dark_gold())
        embed.set_thumbnail(url=self.bot.user.avatar)
        embed.set_footer(text="Thanks for  using the bot!", icon_url=ctx.author.avatar)
        embed.add_field(name="Guilds", value=f"{len(self.bot.guilds)}", inline=True)
        embed.add_field(name="Users", value=f"{len(self.bot.users)}", inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=False)
        embed.add_field(name="OS", value=platform.system(), inline=True)
        embed.set_footer(text=f"Hint : {await Hints.get_single_hint()}", icon_url=await Hints.get_avatar())
        try:
            embed.add_field(name="CPU usage", value=str(round(float(os.popen(
                '''grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {print usage }' ''').readline()),
                                                              2)) + " %", inline=True)
            mem = str(os.popen('free -t -m').readlines())
            mem_G = mem[mem.index('T') + 14:-4]
            S1_ind = mem_G.index(' ')
            mem_G1 = mem_G[S1_ind + 8:]
            S2_ind = mem_G1.index(' ')
            mem_F = mem_G1[S2_ind + 8:]
            embed.add_field(name="RAM available", value=f"{mem_F} MB", inline=True)
        except Exception as e:
            print(e)
        admin = False
        for roles in ctx.author.roles:
            if roles.id == 1020638168237740042:
                admin = True
                embed1 = discord.Embed(title="Status", description="Status of the bot", color=discord.Color.dark_gold())
                embed1.set_thumbnail(url=self.bot.user.avatar)
                embed1.set_footer(text=f"Hint : {await Hints.get_single_hint()}", icon_url=await Hints.get_avatar())
                td = datetime.datetime.utcnow() - self.bot.boot
                td = days_hours_minutes(td)
                embed1.add_field(name="UpTime",
                                 value=f"{str(td[0]) + ' days ' if td[0] > 0 else ''}{str(td[1]) + 'hours ' if td[1] > 0 else ''}{str(td[2]) + ' minutes' if td[2] > 0 else ''}",
                                 inline=False)
                embed1.add_field(name="Tasks Completed",
                                 value=f"{str(int(self.bot.translation_count * 3.1))} MB translated, "
                                       f"{str(self.bot.crawler_count)} crawled", inline=False)
                embed1.add_field(name="Current Tasks", value=f"{len(self.bot.crawler)} Crawl,"
                                                             f" {len(self.bot.translator)} translate", inline=True)
                embed1.add_field(name="Tasks Count", value=str(len(asyncio.all_tasks())), inline=False)
                host = socket.gethostname()
                embed1.add_field(name="Host", value=host, inline=True)
                embed1.add_field(name="IP address", value=socket.gethostbyname(host), inline=True)
                tasks = asyncio.all_tasks()
                print(tasks)
                tasks_str = ""
                count = 0
                for task in tasks:
                    count += 1
                    tasks_str += f"\n{count} -- {task.get_name()} : {str(task.get_coro())}"
                embed2 = discord.Embed(title="Status", description=f"**Tasks running in bot**\n\n {tasks_str[:2400]}",
                                       color=discord.Color.dark_gold())
                embed2.set_thumbnail(url=self.bot.user.avatar)
                embed2.set_footer(text=f"Hint : {await Hints.get_single_hint()}", icon_url=await Hints.get_avatar())
        if admin:
            return await Library.buttons([embed, embed1, embed2], ctx)
        else:
            buttons = {
                "Invite": [self.bot.invite_url, "💖"],
                "Support Server": [
                    "https://discord.gg/EN3ECMHEZP",
                    self.bot.get_emoji(952146686338285588),
                ],
            }
            view = LinkView(buttons)
            return await ctx.send(embed=embed, view=view)

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="Give the progress of all current tasks of the bot(only for bot-admins)... ")
    async def tasks(self, ctx: commands.Context):
        await ctx.defer()
        out = "**Crawler Tasks**\n"
        if not self.bot.crawler.items():
            out = out + "No tasks currently\n"
        else:
            for keys, values in self.bot.crawler.items():
                user = self.bot.get_user(keys)
                user = user.name
                out = f"{out}{user} : {values} \n"
        out = out + "\n**Crawlnext Tasks**\n"
        if not self.bot.crawler_next.items():
            out = out + "No tasks currently\n"
        else:
            for keys, values in self.bot.crawler_next.items():
                user = self.bot.get_user(keys)
                user = user.name
                out = f"{out}{user} : {values} \n"
        out = out + "\n**Translator Tasks**\n"
        if not self.bot.translator.items():
            out = out + "No tasks currently\n"
        else:
            for keys, values in self.bot.translator.items():
                user = self.bot.get_user(keys)
                user = user.name
                out = f"{out}{user} : {values} \n"
        return await ctx.send(embed=discord.Embed(description=out[:3800], colour=discord.Color.random()),
                              ephemeral=True)

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="Give the progress of all current tasks of the bot(only for bot-admins)... ")
    async def update_mega(self, ctx: commands.Context, password: str = None, username: str = None):
        try:
            with open(os.getenv("MEGA"), 'rb') as f:
                megastore = pickle.load(f)
        except:
            pass
        if username is None:
            username = megastore["user"]
        if password is None:
            password = megastore["password"]
        megastore = {"user": username, "password": password}
        self.bot.logger.info(f"Mega password updated by user {ctx.author.name}")
        with open(os.getenv("MEGA"), 'wb') as f:
            pickle.dump(megastore, f)
        try:
            self.bot.mega = Mega().login(username, password)
            await ctx.reply(content=f"connected to mega as {username}")
        except Exception as e:
            await ctx.reply(content=f"Connecting to mega failed due to \n> {e}")
            print(e.with_traceback())
            print(e)
        return

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="update category to all novels")
    async def addcategory(self, ctx: commands.Context, starts: int = 1):
        await ctx.send("> started task")
        txt = ""
        updates = []
        list_nov = await self.bot.mongo.library.get_all_novels()
        for novel in list_nov:
            try:
                if novel._id % 100 == 0:
                    await ctx.send(f"completed {novel._id}")
                title = novel.title
                desc = novel.description
                cat = Categories.from_string(f"{title}")
                if cat == "Uncategorised" and not desc == "":
                    cat = Categories.from_string(f"{title} {desc}")
                if cat != novel.category:
                    updates.append({"_id": novel._id, "category": cat})
                    txt = txt + f"{novel._id} --->  {cat} -> {novel.category}\n"
                    if len(txt) >= 1900:
                        await ctx.send(txt)
                        await self.bot.mongo.library.update_bulk_category(updates)
                        updates = []
                        txt = ""
            except Exception as e:
                await ctx.send(f"> failed in id {novel._id} due to {e}")

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="update category to all novels")
    async def create(self, ctx: commands.Context):
        pic_dict: [int, str] = dict()
        for i in range(0, 101):
            file = discord.File(f"utils/img/{i}.png")
            msg = await ctx.send(file=file)
            pic_dict[i] = msg.attachments[0].url
        print(pic_dict)
        # return await ctx.send(pic_dict)

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="ban user.. Admin only command")
    async def getlog(self, ctx: commands.Context, file: bool = False, no: int = 1500):
        log_path = self.bot.log_path
        if not log_path:
            return await ctx.send("> Log file path not set or logging not initialized.")
        # Ensure log file exists
        if not os.path.exists(log_path):
            try:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception as e:
                return await ctx.send(f"> Could not create log file: {e}")
        # Flush all handlers so the file is up-to-date
        for handler in getattr(self.bot.logger, 'handlers', []):
            try:
                handler.flush()
            except Exception:
                pass
        try:
            import aiofiles.os
            stat = await aiofiles.os.stat(log_path)
            file_size = stat.st_size
            # Read only last N bytes if file is large
            read_bytes = min(no, file_size)
            async with aiofiles.open(log_path, "r", encoding="utf-8") as f:
                if file_size > no:
                    await f.seek(file_size - no)
                full = await f.read()
        except Exception as e:
            return await ctx.send(f"> Error reading log file: {e}")
        if not full.strip():
            return await ctx.send("> Log file is empty.")
        # Truncate for Discord limits
        last_bytes = full[-no:]
        if file:
            try:
                return await ctx.send(
                    embed=discord.Embed(title="logs", description=last_bytes[:4000], colour=discord.Colour.random()),
                    file=discord.File(log_path, "discord_botLog.txt"),
                )
            except Exception as e:
                return await ctx.send(f"> Error sending log file: {e}")
        else:
            return await ctx.send(f"```yaml\n{last_bytes[:1900]}\n```")

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="Run diagnostics and health checks on bot subsystems.")
    async def diagnostics(self, ctx: commands.Context):
        await ctx.defer()
        results = []
        # MongoDB check
        try:
            await self.bot.mongo.library.get_all_novels()
            mongo_status = "✅ MongoDB: OK"
        except Exception as e:
            mongo_status = f"❌ MongoDB: {e}"
        results.append(mongo_status)
        # MEGA check
        try:
            if self.bot.mega and hasattr(self.bot.mega, 'user'):
                mega_status = f"✅ MEGA: Logged in as {self.bot.mega.user}"
            else:
                mega_status = "❌ MEGA: Not logged in"
        except Exception as e:
            mega_status = f"❌ MEGA: {e}"
        results.append(mega_status)
        # Discord API check
        try:
            latency = round(self.bot.latency * 1000)
            discord_status = f"✅ Discord API: Latency {latency}ms"
        except Exception as e:
            discord_status = f"❌ Discord API: {e}"
        results.append(discord_status)
        # File system check
        try:
            test_path = os.path.join(os.path.dirname(__file__), '../logs/diag_test.txt')
            with open(test_path, 'w') as f:
                f.write('test')
            os.remove(test_path)
            fs_status = "✅ File System: Write OK"
        except Exception as e:
            fs_status = f"❌ File System: {e}"
        results.append(fs_status)
        # Backup dir check
        try:
            backup_dir = os.path.join(os.path.dirname(__file__), '../backups')
            os.makedirs(backup_dir, exist_ok=True)
            test_file = os.path.join(backup_dir, 'diag_test.txt')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            backup_status = "✅ Backups Dir: Write OK"
        except Exception as e:
            backup_status = f"❌ Backups Dir: {e}"
        results.append(backup_status)
        await ctx.send("\n".join(results))

    @commands.has_role(1020638168237740042)
    @commands.hybrid_command(help="Trigger a backup of the MongoDB database.")
    async def backupdb(self, ctx: commands.Context):
        await ctx.defer()
        import subprocess
        import datetime
        backup_dir = os.path.join(os.path.dirname(__file__), '../backups')
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.abspath(os.path.join(backup_dir, f"mongo_backup_{timestamp}.archive"))
        # Try to get Mongo URI from env or config
        mongo_uri = os.getenv("MONGO_URI") or getattr(self.bot.mongo, 'uri', None)
        if not mongo_uri:
            return await ctx.send("> Could not determine MongoDB URI for backup.")
        try:
            result = subprocess.run([
                "mongodump",
                f"--uri={mongo_uri}",
                f"--archive={backup_file}"
            ], capture_output=True, text=True)
            if result.returncode == 0:
                await ctx.send(f"✅ Backup complete: `{backup_file}`")
            else:
                await ctx.send(f"❌ Backup failed: {result.stderr}")
        except Exception as e:
            await ctx.send(f"❌ Backup error: {e}")

    # Auto-backup task (runs daily)
    @commands.Cog.listener()
    async def on_ready(self):
        if not hasattr(self, '_auto_backup_started'):
            self._auto_backup_started = True
            self.bot.loop.create_task(self.auto_backup_task())

    async def auto_backup_task(self):
        import asyncio
        import datetime
        while True:
            now = datetime.datetime.utcnow()
            # Run at 03:00 UTC
            next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if next_run < now:
                next_run += datetime.timedelta(days=1)
            await asyncio.sleep((next_run - now).total_seconds())
            ctx = None
            try:
                # Use backupdb logic, but without ctx
                backup_dir = os.path.join(os.path.dirname(__file__), '../backups')
                os.makedirs(backup_dir, exist_ok=True)
                timestamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
                backup_file = os.path.abspath(os.path.join(backup_dir, f"mongo_backup_{timestamp}.archive"))
                mongo_uri = os.getenv("MONGO_URI") or getattr(self.bot.mongo, 'uri', None)
                if mongo_uri:
                    import subprocess
                    result = subprocess.run([
                        "mongodump",
                        f"--uri={mongo_uri}",
                        f"--archive={backup_file}"
                    ], capture_output=True, text=True)
                    # Optionally, prune old backups (keep last 7)
                    backups = sorted([f for f in os.listdir(backup_dir) if f.startswith('mongo_backup_')], reverse=True)
                    for old in backups[7:]:
                        try:
                            os.remove(os.path.join(backup_dir, old))
                        except Exception:
                            pass
            except Exception as e:
                if ctx:
                    await ctx.send(f"❌ Auto-backup error: {e}")


async def setup(bot):
    await bot.add_cog(Admin(bot))
