import random
from typing import Optional

import discord
from redbot.core import Config, commands
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

from .defaults import BOT_INSULTS, DEFAULT_INSULTS

_ = Translator("Insult", __file__)

MAX_LENGTH = 1500


@cog_i18n(_)
class Insult(commands.Cog):
    """Insult people in a creative way, with insults managed from Discord."""

    __author__ = ["Airen", "JennJenn", "TrustyJAID"]
    __version__ = "2.0.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8273645091, force_registration=True)
        self.config.register_guild(
            insults={},  # {"1": "text", ...} - IDs stay stable when others are removed
            next_id=1,
            use_defaults=True,
        )

    def format_help_for_context(self, ctx: commands.Context) -> str:
        pre_processed = super().format_help_for_context(ctx)
        return f"{pre_processed}\n\nCog Version: {self.__version__}"

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete (no user data is stored)."""
        return

    async def _pool(self, guild: Optional[discord.Guild]) -> list:
        if guild is None:
            return list(DEFAULT_INSULTS)
        data = await self.config.guild(guild).all()
        pool = list(DEFAULT_INSULTS) if data["use_defaults"] else []
        pool.extend(data["insults"].values())
        return pool

    # ------------------------------------------------------------------ #
    # Main command
    # ------------------------------------------------------------------ #

    @commands.command(aliases=["takeitback"])
    async def insult(self, ctx: commands.Context, user: discord.Member = None) -> None:
        """
        Insult the user

        `user` the user you would like to insult
        """
        pool = await self._pool(ctx.guild)
        if not pool:
            await ctx.send(_("There are no insults available. Add some with `{p}insultset add`.").format(p=ctx.clean_prefix))
            return

        target = user or ctx.author
        if user and user.id == self.bot.user.id:
            target = ctx.author
            text = random.choice(BOT_INSULTS)
        else:
            text = random.choice(pool)

        # Only allow the target to be pinged, never @everyone/roles from custom text.
        await ctx.send(
            f"{target.mention} {text}",
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[target]
            ),
        )

    # ------------------------------------------------------------------ #
    # Management commands
    # ------------------------------------------------------------------ #

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def insultset(self, ctx: commands.Context) -> None:
        """Manage this server's custom insults."""

    @insultset.command(name="add")
    async def insultset_add(self, ctx: commands.Context, *, text: str) -> None:
        """Add a custom insult."""
        if len(text) > MAX_LENGTH:
            await ctx.send(_("That's too long (max {n} characters).").format(n=MAX_LENGTH))
            return
        async with self.config.guild(ctx.guild).all() as data:
            new_id = data["next_id"]
            data["insults"][str(new_id)] = text
            data["next_id"] = new_id + 1
        await ctx.send(_("Added insult #{id}.").format(id=new_id))

    @insultset.command(name="edit")
    async def insultset_edit(self, ctx: commands.Context, insult_id: int, *, text: str) -> None:
        """Edit a custom insult by its ID (see `insultset list`)."""
        if len(text) > MAX_LENGTH:
            await ctx.send(_("That's too long (max {n} characters).").format(n=MAX_LENGTH))
            return
        async with self.config.guild(ctx.guild).insults() as insults:
            if str(insult_id) not in insults:
                await ctx.send(_("No custom insult with ID {id}.").format(id=insult_id))
                return
            insults[str(insult_id)] = text
        await ctx.send(_("Updated insult #{id}.").format(id=insult_id))

    @insultset.command(name="remove", aliases=["delete", "del"])
    async def insultset_remove(self, ctx: commands.Context, insult_id: int) -> None:
        """Remove a custom insult by its ID."""
        async with self.config.guild(ctx.guild).insults() as insults:
            if insults.pop(str(insult_id), None) is None:
                await ctx.send(_("No custom insult with ID {id}.").format(id=insult_id))
                return
        await ctx.send(_("Removed insult #{id}.").format(id=insult_id))

    @insultset.command(name="list")
    async def insultset_list(self, ctx: commands.Context) -> None:
        """List this server's custom insults."""
        insults = await self.config.guild(ctx.guild).insults()
        if not insults:
            await ctx.send(_("No custom insults yet. Add one with `{p}insultset add`.").format(p=ctx.clean_prefix))
            return
        lines = "\n".join(f"{i}. {t}" for i, t in sorted(insults.items(), key=lambda kv: int(kv[0])))
        pages = [box(p) for p in pagify(lines, page_length=1800)]
        if len(pages) == 1:
            await ctx.send(pages[0])
        else:
            await menu(ctx, pages, DEFAULT_CONTROLS)

    @insultset.command(name="defaults")
    async def insultset_defaults(self, ctx: commands.Context, enabled: bool) -> None:
        """Choose whether the built-in insults are used alongside your custom ones."""
        await self.config.guild(ctx.guild).use_defaults.set(enabled)
        if enabled:
            await ctx.send(_("Built-in insults are now enabled."))
        else:
            await ctx.send(_("Built-in insults are now disabled. Only custom insults will be used."))
