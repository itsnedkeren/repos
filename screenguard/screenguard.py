import logging
import statistics
import time
from collections import defaultdict, deque

import discord
from discord.ext import tasks
from redbot.core import Config, commands

log = logging.getLogger("red.screenguard")

SAMPLE_INTERVAL = 10  # seconds between occupancy samples


class ScreenGuard(commands.Cog):
    """Deny Stream permission to a role while voice channels are crowded."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x5C2EE9A1, force_registration=True)
        self.config.register_guild(
            channels=[],     # monitored voice channel IDs
            role=None,       # role ID that loses Stream
            threshold=20,    # median occupancy that triggers the lock
            window=120,      # seconds the median is computed over
            restore=True,    # unlock when median drops below threshold
            locked={},       # {channel_id: previous overwrite value (True/False/None)}
        )
        self.samples = defaultdict(deque)  # channel_id -> deque[(monotonic_ts, count)]
        self._warned = set()
        self.sampler.start()

    def cog_unload(self):
        self.sampler.cancel()

    # ---------- core loop ----------

    @tasks.loop(seconds=SAMPLE_INTERVAL)
    async def sampler(self):
        now = time.monotonic()
        for guild in self.bot.guilds:
            try:
                await self._check_guild(guild, now)
            except Exception:
                log.exception("Error while checking guild %s", guild.id)

    @sampler.before_loop
    async def _before(self):
        await self.bot.wait_until_red_ready()

    def _median(self, channel_id, now, window):
        buf = self.samples[channel_id]
        while buf and now - buf[0][0] > window:
            buf.popleft()
        # require the window to be (almost) fully covered before judging
        if not buf or now - buf[0][0] < window * 0.9:
            return None
        return statistics.median(c for _, c in buf)

    async def _check_guild(self, guild, now):
        conf = await self.config.guild(guild).all()
        role = guild.get_role(conf["role"]) if conf["role"] else None
        if role is None or not conf["channels"]:
            return

        for cid in conf["channels"]:
            channel = guild.get_channel(cid)
            if not isinstance(channel, discord.VoiceChannel):
                continue
            self.samples[cid].append((now, len(channel.members)))
            med = self._median(cid, now, conf["window"])
            if med is None:
                continue

            is_locked = str(cid) in conf["locked"]
            if med >= conf["threshold"] and not is_locked:
                await self._lock(channel, role, med)
            elif med < conf["threshold"] and is_locked and conf["restore"]:
                await self._unlock(channel, role, med)

    # ---------- lock / unlock ----------

    async def _lock(self, channel, role, med):
        ow = channel.overwrites_for(role)
        prev = ow.stream
        ow.stream = False
        try:
            await channel.set_permissions(
                role, overwrite=ow, reason=f"ScreenGuard: median occupancy {med}"
            )
        except discord.HTTPException as e:
            if channel.id not in self._warned:
                self._warned.add(channel.id)
                log.warning("Could not lock %s: %s", channel.id, e)
            return
        self._warned.discard(channel.id)
        async with self.config.guild(channel.guild).locked() as locked:
            locked[str(channel.id)] = prev

    async def _unlock(self, channel, role, med=None):
        async with self.config.guild(channel.guild).locked() as locked:
            prev = locked.pop(str(channel.id), None)
        ow = channel.overwrites_for(role)
        ow.stream = prev
        try:
            await channel.set_permissions(
                role,
                overwrite=None if ow.is_empty() else ow,
                reason="ScreenGuard: occupancy back under threshold",
            )
        except discord.HTTPException as e:
            log.warning("Could not unlock %s: %s", channel.id, e)

    # ---------- commands ----------

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def screenguard(self, ctx):
        """Configure ScreenGuard."""

    @screenguard.command()
    async def addchannel(self, ctx, channel: discord.VoiceChannel):
        """Monitor a voice channel."""
        async with self.config.guild(ctx.guild).channels() as chans:
            if channel.id not in chans:
                chans.append(channel.id)
        await ctx.tick()

    @screenguard.command()
    async def removechannel(self, ctx, channel: discord.VoiceChannel):
        """Stop monitoring a voice channel (unlocks it if locked)."""
        conf = await self.config.guild(ctx.guild).all()
        role = ctx.guild.get_role(conf["role"]) if conf["role"] else None
        if role and str(channel.id) in conf["locked"]:
            await self._unlock(channel, role)
        async with self.config.guild(ctx.guild).channels() as chans:
            if channel.id in chans:
                chans.remove(channel.id)
        self.samples.pop(channel.id, None)
        await ctx.tick()

    @screenguard.command()
    async def role(self, ctx, role: discord.Role):
        """Set the role that loses Stream permission."""
        await self.config.guild(ctx.guild).role.set(role.id)
        await ctx.tick()

    @screenguard.command()
    async def threshold(self, ctx, amount: int):
        """Median occupancy that triggers the lock (default 20)."""
        if amount < 1:
            return await ctx.send("Must be at least 1.")
        await self.config.guild(ctx.guild).threshold.set(amount)
        await ctx.tick()

    @screenguard.command()
    async def window(self, ctx, seconds: int):
        """Seconds the median is computed over (30-900, default 120)."""
        if not 30 <= seconds <= 900:
            return await ctx.send("Pick a value between 30 and 900.")
        await self.config.guild(ctx.guild).window.set(seconds)
        await ctx.tick()

    @screenguard.command()
    async def restore(self, ctx, enabled: bool):
        """Re-enable streaming automatically when occupancy drops again."""
        await self.config.guild(ctx.guild).restore.set(enabled)
        await ctx.tick()

    @screenguard.command()
    async def status(self, ctx):
        """Show settings and current medians."""
        conf = await self.config.guild(ctx.guild).all()
        role = ctx.guild.get_role(conf["role"]) if conf["role"] else None
        now = time.monotonic()
        lines = [
            f"Role: {role.mention if role else 'not set'}",
            f"Threshold: {conf['threshold']} | Window: {conf['window']}s | "
            f"Auto-restore: {conf['restore']}",
        ]
        for cid in conf["channels"]:
            ch = ctx.guild.get_channel(cid)
            med = self._median(cid, now, conf["window"])
            state = "LOCKED" if str(cid) in conf["locked"] else "open"
            lines.append(
                f"{ch.mention if ch else cid}: median "
                f"{'warming up' if med is None else med} ({state})"
            )
        await ctx.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())
