import datetime
import random

import discord
import joblib
from discord import app_commands
from discord.ext import commands
from reactionmenu import ViewButton, ViewMenu

from core.bot import Raizel
from databases.data import Novel
from utils.category import Categories
from utils.hints import Hints


class Library(commands.Cog):
    def __init__(self, bot: Raizel) -> None:
        self.bot = bot
        # self.titles = joblib.load('titles.sav')
        self.sorted_data: list = ["_id", "title", "rating", "size", "uploader", "date"]
        self.list_page_size: int = 12
        self.list_page_char_limit: int = 3500

    @staticmethod
    def common_elements_finder(*args):
        if len(args) == 1:
            return args[0]
        initial = args[0]
        for arg in args[1:]:
            initial = [i for i in initial for j in arg if i._id == j._id]
        return initial

    @staticmethod
    async def buttons(lst: list[discord.Embed], ctx: commands.Context) -> None:
        if len(lst) == 1:
            await ctx.send(embed=lst[0])
            return
        menu = ViewMenu(
            ctx,
            menu_type=ViewMenu.TypeEmbed,
            remove_buttons_on_timeout=True,
            timeout=60,
        )
        menu.add_pages(lst)

        # ── First / Last (secondary – edge navigation) ──────────────────────
        fb = ViewButton(
            style=discord.ButtonStyle.secondary,
            emoji="<:DoubleArrowLeft:989134953142956152>",
            custom_id=ViewButton.ID_GO_TO_FIRST_PAGE,
        )
        ff = ViewButton(
            style=discord.ButtonStyle.secondary,
            emoji="<:DoubleArrowRight:989134892384256011>",
            custom_id=ViewButton.ID_GO_TO_LAST_PAGE,
        )

        # ── Prev / Next (primary – main navigation) ──────────────────────────
        back = ViewButton(
            style=discord.ButtonStyle.primary,
            emoji="<:ArrowLeft:989134685068202024>",
            custom_id=ViewButton.ID_PREVIOUS_PAGE,
        )
        after = ViewButton(
            style=discord.ButtonStyle.primary,
            emoji="<:rightArrow:989136803284004874>",
            custom_id=ViewButton.ID_NEXT_PAGE,
        )

        # ── Stop (danger – clearly a destructive / close action) ─────────────
        stop = ViewButton(
            style=discord.ButtonStyle.danger,
            emoji="<:dustbin:989150297333043220>",
            custom_id=ViewButton.ID_END_SESSION,
        )

        # Layout: ◀◀  ◀  🗑  ▶  ▶▶
        menu.add_button(fb)
        menu.add_button(back)
        menu.add_button(stop)
        menu.add_button(after)
        menu.add_button(ff)
        return await menu.start()

    async def make_base_embed(
            self,
            data: Novel,
            *,
            position: int | None = None,
            total: int | None = None,
    ) -> discord.Embed:
        title = self._compact_title(data["title"], max_len=120)
        description = (data.get("description") or "No description.").strip()
        if len(description) > 700:
            description = f"{description[:697]}..."

        embed = discord.Embed(
            title=f"#{data['_id']} • {title}",
            url=data["download"],
            description=f"> {description}",
            color=discord.Color.blurple(),
        )

        size_mb = round(data["size"] / (1024 ** 2), 2)
        rating = int(data.get("rating", 0))
        stars = "⭐" * rating if rating > 0 else "No rating"

        embed.add_field(name="Category", value=data["category"], inline=True)
        embed.add_field(name="Language", value=data["language"], inline=True)
        embed.add_field(name="Size", value=f"{size_mb} MB", inline=True)
        embed.add_field(name="Rating", value=stars, inline=True)

        if not str(data.get("org_language", "na")).lower() == "na":
            embed.add_field(name="Raw Language", value=data["org_language"], inline=True)

        tags = data.get("tags") or []
        if tags:
            tag_preview = ", ".join(tags[:10])
            if len(tags) > 10:
                tag_preview += ", ..."
            embed.add_field(name="Tags", value=f"`{tag_preview}`", inline=False)

        uploader = self.bot.get_user(data["uploader"]) or await self.bot.fetch_user(data["uploader"])
        try:
            if data.get("crawled_from"):
                embed.add_field(name="Source", value=f"{data['crawled_from']}", inline=False)
        except:
            pass

        embed.add_field(
            name="Uploader",
            value=(
                f"Uploaded by {uploader}\n"
                f"{discord.utils.format_dt(datetime.datetime.fromtimestamp(data['date']), style='R')}"
            ),
            inline=False,
        )

        if data.get("thumbnail"):
            embed.set_thumbnail(url=data["thumbnail"])

        footer_prefix = f"Result {position}/{total} • " if position is not None and total is not None else ""
        embed.set_footer(
            text=f"{footer_prefix}Hint: {await Hints.get_single_hint()}",
            icon_url=await Hints.get_avatar(),
        )
        return embed

    async def make_list_embed(self, data: list[Novel]) -> list[discord.Embed]:
        embeds = []
        total = len(data)
        for idx, novel in enumerate(data, start=1):
            embeds.append(await self.make_base_embed(novel, position=idx, total=total))
        return embeds

    async def make_base_list_embed(
            self,
            data: list[Novel],
            page: int,
            total_pages: int,
            total_results: int,
            start_index: int,
    ) -> discord.Embed:
        rows = [self._build_list_row(novel, start_index + idx) for idx, novel in enumerate(data, start=1)]
        description = "\n\n".join(rows)
        embed = discord.Embed(
            title=f"📚 Library  —  {total_results} result{'s' if total_results != 1 else ''}",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=(
                f"Page {page}/{total_pages}  •  /library info <id> for details  •  "
                f"Hint: {await Hints.get_single_hint()}"
            ),
            icon_url=await Hints.get_avatar(),
        )
        return embed

    def _build_list_row(self, novel: Novel, index: int) -> str:
        title = self._compact_title(novel["title"])
        size_mb = round(novel["size"] / (1024 ** 2), 2)
        rating = int(novel.get("rating", 0))
        stars = "⭐" * rating if rating > 0 else "—"
        category = novel.get("category", "").strip()
        cat_badge = f" `{category}`" if category else ""
        return (
            f"**{index}.** [{title}]({novel['download']})\n"
            f"`#{novel['_id']}` • `{novel['language']}`{cat_badge} • `{size_mb} MB` • {stars}"
        )

    @staticmethod
    def _compact_title(title: str, max_len: int = 72) -> str:
        clean = title.split("__")[0].strip().replace("\n", " ")
        return clean if len(clean) <= max_len else f"{clean[:max_len - 1]}..."

    async def make_list_embed_list(self, data: list[Novel]) -> list[discord.Embed]:
        embeds = []
        pages: list[list[Novel]] = []
        current_page: list[Novel] = []
        current_chars = 0

        # Pack results by both item limit and character budget to keep embeds readable.
        for novel in data:
            preview_row = self._build_list_row(novel, 999)
            projected = current_chars + len(preview_row) + 2
            if current_page and (
                    len(current_page) >= self.list_page_size or projected > self.list_page_char_limit
            ):
                pages.append(current_page)
                current_page = []
                current_chars = 0
            current_page.append(novel)
            current_chars += len(preview_row) + 2

        if current_page:
            pages.append(current_page)

        total_pages = max(len(pages), 1)
        total_results = len(data)
        start_index = 0
        for page_no, page_data in enumerate(pages, start=1):
            embeds.append(
                await self.make_base_list_embed(
                    page_data,
                    page=page_no,
                    total_pages=total_pages,
                    total_results=total_results,
                    start_index=start_index,
                )
            )
            start_index += len(page_data)

        return embeds

    @commands.hybrid_group()
    async def library(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @library.command(name="search",
                     help="searches a novel in library. Shuffle is turned on by default. Use sort_by for sorting novels")
    async def search(
            self,
            ctx: commands.Context,
            title: str = None,
            language: str = None,
            rating: int = 0,
            show_list: bool = True,
            category: str = None,
            tags: str = None,
            raw_language: str = None,
            size: float = None,
            uploader: discord.User = None,
            shuffle: bool = True,
            sort_by: str = None,
            no_of_novels: int = 300,
    ) -> None:
        """Searches a novel in library
               Parameters
               ----------
               ctx : commands.Context
                   The interaction
               title : str, optional
                   Title of the novel, bot will auto-suggest novels in library
               language :
                    Language of the novel
               rating :
                    rating of the novel must be between 0 to 5
               show_list :
                    give true if you need it in list view by default it  is false
               category :
                    category of novel
               tags :
                    tags of novel , bot will auto suggest
               raw_language :
                    raw_language of novel
               size :
                    size of novel in mb, bot will give novel with more than this size
               uploader :
                    uploader of novel
               shuffle :
                    will shuffle the novel, by default true, if you don't need to shuffle give false
               sort_by :
                    sort the novels according to the value given
               no_of_novels :
                    number of novels you want to get from library
               """
        try:
            await ctx.defer()
        except:
            pass
        msg = await ctx.send("Searching...")
        tags = [i.strip() for i in tags.split() if i] if tags else None
        if uploader:
            uploader_id = uploader.id
        else:
            uploader_id = None
        if show_list is True and no_of_novels == 300:
            no_of_novels = 1000
        allnovels = await self.bot.mongo.library.find_common(title=title, tag=tags, rating=rating, category=category, language=language, size=size, original_language=raw_language, uploader=uploader_id, no=no_of_novels)
        if not allnovels or allnovels == []:
            await ctx.send("> **No results found.**")
            await msg.delete()
            return

        if shuffle and sort_by is None:
            random.shuffle(allnovels)
        if sort_by is not None:
            if sort_by not in self.sorted_data:
                await ctx.send(f"> **Given sort by is not present in bot. available filters \n {self.sorted_data}**")
            else:
                if sort_by == "_id":
                    allnovels.sort(key=lambda x: x["_id"])
                elif sort_by == "title":
                    allnovels.sort(key=lambda x: x["title"])
                elif sort_by == "rating":
                    allnovels.sort(key=lambda x: x["rating"])
                    allnovels.reverse()
                elif sort_by == "size":
                    allnovels.sort(key=lambda x: x["size"])
                    allnovels.reverse()
                elif sort_by == "uploader":
                    allnovels.sort(key=lambda x: x["uploader"])
                elif sort_by == "date":
                    allnovels.sort(key=lambda x: x["date"])
                    allnovels.reverse()
        # print("got all novels")
        full_size = 0
        if not allnovels:
            return await ctx.send("> **No results found.**")
        if len(allnovels) >= no_of_novels:
            full_size = len(allnovels)
            allnovels = allnovels[:no_of_novels]
        if show_list:
            embeds = await self.make_list_embed_list(allnovels)
            try:
                del allnovels
            except:
                pass
            await msg.delete()
            return await self.buttons(embeds, ctx)
        else:
            embeds = await self.make_list_embed(allnovels)
            try:
                del allnovels
            except:
                pass
            await msg.delete()
            return await self.buttons(embeds, ctx)

    @library.command(name="random", help="Gives 10 random novel in library.")
    async def random(
            self,
            ctx: commands.Context, no_of_novels: int = 10, language: str = "english"
    ) -> None:
        """get random novels from library
               Parameters
               ----------
               ctx : commands.Context
                   The interaction
               no_of_novels : int, optional
                   number of novels , by default it is 10
               language: str, optional
                    language to be filtered
               """
        await ctx.defer()
        novels = await self.bot.mongo.library.get_random_novel(no=no_of_novels, language=language)
        embeds = await self.make_list_embed(novels)
        return await self.buttons(embeds, ctx)

    @search.autocomplete("language")
    async def translate_complete(
            self, inter: discord.Interaction, language: str
    ) -> list[app_commands.Choice]:
        lst = [i for i in self.bot.all_langs if language.lower() in i.lower()][:25]
        return [app_commands.Choice(name=i, value=i) for i in lst]

    @search.autocomplete("raw_language")
    async def translate_complete(
            self, inter: discord.Interaction, language: str
    ) -> list[app_commands.Choice]:
        lst = [i for i in self.bot.all_langs if language.lower() in i.lower()][:25]
        return [app_commands.Choice(name=i, value=i) for i in lst]

    @search.autocomplete("category")
    async def translate_complete(
            self, inter: discord.Interaction, category: str
    ) -> list[app_commands.Choice]:
        lst = [str(cat.value.name) for cat in Categories if cat.value.name.lower().startswith(category.lower())][0:25]
        return [app_commands.Choice(name=i, value=i) for i in lst]

    @search.autocomplete("sort_by")
    async def translate_complete(
            self, inter: discord.Interaction, language: str
    ) -> list[app_commands.Choice]:
        lst = self.sorted_data
        return [app_commands.Choice(name=i, value=i) for i in lst]

    @search.autocomplete("tags")
    async def translate_complete(
            self, inter: discord.Interaction, tag: str
    ) -> list[app_commands.Choice]:
        lst = [
                  i
                  for i in await self.bot.mongo.library.get_all_tags
                  if tag.lower() in i.lower()
              ][:25]
        return [app_commands.Choice(name=i, value=i) for i in lst]

    @search.autocomplete("title")
    async def translate_complete(
            self, inter: discord.Interaction, title: str
    ) -> list[app_commands.Choice]:
        if not hasattr(self, 'titles'):
            self.titles = joblib.load('titles.sav')
        lst = [
                  str(i[:90]).strip()
                  for i in self.titles
                  if title.lower() in i.lower()
              ][:25]
        # print(lst)
        return [app_commands.Choice(name=i, value=i) for i in lst]

    @library.command(name="info", help="shows info about a novel.")
    async def info(self, ctx: commands.Context, _id: int) -> None:
        """shows info of the novel.
                       Parameters
                       ----------
                       ctx : commands.Context
                           The interaction
                       _id : int
                           library id which you want to view
                       """
        try:
            await ctx.defer()
        except:
            pass
        novel = await self.bot.mongo.library.get_novel_by_id(_id)
        if not novel:
            return await ctx.send("No novel found.")
        embed = await self.make_base_embed(novel)
        return await ctx.send(embed=embed)

    @library.command(name="review", help="reviews a novel.")
    async def review(
            self, ctx: commands.Context, _id: int, rating: int, summary: str
    ) -> None:
        """Review a novel.
               Parameters
               ----------
               ctx : commands.Context
                   The interaction
               _id : int
                   library id which you want to review
               rating : int
                    give your rating from 0 to 5
               summary : str
                    your review comments.
               """
        await ctx.defer()
        if not 0 <= rating <= 5:
            await ctx.send("Rating must be between 0 and 5.")
            return
        novel = await self.bot.mongo.library.get_novel_by_id(_id)
        if not novel:
            await ctx.send("No novel found.")
            return
        description = novel["description"][:500]
        await self.bot.mongo.library.update_description(
            novel["_id"], f"{description}\n\n**{summary} +  • Reviewed by {ctx.author}**"
        )
        if novel["rating"] != 0:
            rating = int((rating + novel["rating"])/2)
        await self.bot.mongo.library.update_rating(novel["_id"], rating)
        await ctx.send("Novel reviewed.")
        await self.bot.get_command("library info").callback(Library(self.bot), ctx, _id)
        channel = await self.bot.fetch_channel(974673230826721290)
        if channel:
            msg = await channel.send(content=f"> {ctx.author} reviewed novel with id #{_id}")
            context = await self.bot.get_context(msg)
            await self.bot.get_command("library info").callback(Library(self.bot), context, _id)

    @commands.hybrid_command(name="leaderboard", description="Get the bot's leaderboard.")
    async def leaderboard(self, ctx: commands.Context, user: discord.User = None) -> None:
        """Check the leaderboard of a user
        Parameters
        ----------
        ctx : commands.Context
            The interaction
        user : discord.User, optional
            The user to check the leaderboard of, by default None
        """
        await ctx.defer()
        if user is None:
            ld_user_id = ctx.author.id
        else:
            ld_user_id = user.id
        user_rank = await self.bot.mongo.library.get_user_novel_count(user_id=ld_user_id)
        top_200 = await self.bot.mongo.library.get_user_novel_count(_top_200=True)
        embeds = []
        top_200 = [(user_id, count) for user_id, count in top_200.items() if user_id != 925597069748621353]
        chunks = [top_200[i: i + 10] for i in range(0, len(top_200), 10)]
        n = 1
        for chunk in chunks:
            embed = discord.Embed(
                title="Leaderboard",
                description=f"**Leaderboard of the bot!**\
                        \n\n**User Rank: {user_rank[ld_user_id]}**",
                color=discord.Color.random(),
            )
            embed.set_footer(text="Thanks for using TranslationBot!", icon_url=self.bot.user.display_avatar)
            embed.set_thumbnail(url=ctx.author.display_avatar)
            for user_id, count in chunk:
                try:
                    embed.add_field(
                        name=f"{n}. {count} novels",
                        value=f"**{(self.bot.get_user(user_id)).global_name} **-> <@{user_id}>",
                        inline=False,
                    )
                except:
                    pass
                n += 1
            embeds.append(embed)
        return await self.buttons(embeds, ctx)


async def setup(bot: Raizel) -> None:
    await bot.add_cog(Library(bot))
