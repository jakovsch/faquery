import asyncio, asyncstdlib as A, chromadb, discord
import threading, typing, re, requests
from requests.adapters import HTTPAdapter, Retry
from concurrent.futures import ThreadPoolExecutor
from discord.utils import utcnow, as_chunks
from discord.ext.tasks import loop
from discord.ext.commands import (
    Bot, Cog, Context,
    group, command, guild_only, has_permissions,
    CheckFailure, CommandError,
)

from ..db import Database, Row
from ..config import (
    DEBUG,
    EMBEDAPI,
    BOT_PREFIX,
    db_poolsize,
    msg_interval,
    msg_chunksize,
    emb_metadata,
    vecdb_config,
)

Channel = typing.Optional[typing.Union[
    discord.TextChannel,
    discord.Thread,
]]

class FAQuery(Cog):

    def __init__(self, bot: Bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.db = Database(db_poolsize)
        self.vecdb = chromadb.Client(
            chromadb.Settings(**vecdb_config),
        )
        self.workers = ThreadPoolExecutor(
            initializer=Worker.init,
        )

    @group(invoke_without_command=False)
    @guild_only()
    @has_permissions(manage_messages=True)
    async def faq(self, _):
        pass

    @faq.command()
    async def list(self, ctx: Context):
        to_str = lambda r: ' | '.join((
            '🔄' if r['enabled'] else '⏸',
            f'{self.bot.get_channel(r["id"]).mention}',
            f'`{msgs[r["id"]]}`',
            f'`{r["lastsync"].strftime("%d.%m. %H:%M")}`',
        ))
        rows = await A.tuple(await self.db.select(
            'channel', where='guild = :id', vals={'id': ctx.guild.id},
        ))
        msgs = {
            r['id']: self.vecdb.get_collection(str(r['id'])).count()
            for r in rows
        }
        embed = discord.Embed(
            title='🔎 📨 __**Status FAQ Indeksa**__',
            description=f'Za server: **{ctx.guild.name}**',
        ).add_field(
            name='**Status | Kanal | Poruke | Vrijeme**',
            value='\n'.join(map(to_str, rows)),
        ).set_footer(
            text=f'Ukupno {sum(msgs.values())} poruka u {len(rows)} kanala',
        )
        await ctx.send(embed=embed)

    @faq.command()
    async def enable(self, ctx: Context, channel: Channel):
        channel = channel or ctx.channel
        if ctx.guild.id != channel.guild.id:
            raise CheckFailure('Neispravan kanal')
        await self.db_update_insert(channel.id, ctx.guild.id, True)
        await ctx.message.add_reaction('✅')

    @faq.command()
    async def disable(self, ctx: Context, channel: Channel):
        channel = channel or ctx.channel
        if ctx.guild.id != channel.guild.id:
            raise CheckFailure('Neispravan kanal')
        await self.db_update_insert(channel.id, ctx.guild.id, False)
        await ctx.message.add_reaction('✅')

    @faq.command()
    async def forget(self, ctx: Context, channel: Channel):
        channel = channel or ctx.channel
        if ctx.guild.id != channel.guild.id:
            raise CheckFailure('Neispravan kanal')
        await self.db_delete(channel.id)
        await ctx.message.add_reaction('✅')

    @faq.command()
    async def sync(self, ctx: Context, channel: Channel):
        channel = channel or ctx.channel
        if ctx.guild.id != channel.guild.id:
            raise CheckFailure('Neispravan kanal')
        async with ctx.typing(), self.lock:
            row = await self.db.get(
                'channel', where='id = :id', vals={'id': channel.id},
            )
            if row is None:
                raise CommandError('Kanal nije u Indeksu')
            await self.collector(row)
            await ctx.message.add_reaction('✅')

    @command(aliases=('q', 'ask'), rest_is_raw=True)
    async def query(self, ctx: Context, num: typing.Optional[int] = 4, *, str):
        to_str = lambda id, dist, meta, doc: ' '.join((
            f'{ctx.channel.get_partial_message(id).jump_url}',
            f'`{1-dist:.3f}`',
        ))
        id = ctx.channel.id
        async with ctx.typing():
            row = await self.db.get(
                'channel', where='id = :id', vals={'id': id},
            )
            if row is None:
                raise CommandError('Kanal nije u Indeksu')
            res = await self.bot.loop.run_in_executor(
                self.workers, Worker.query, id, str, num,
            )
            embed = discord.Embed(
                description='\n'.join(map(to_str, *res.values())) or '404',
            )
            await ctx.send(embed=embed, delete_after=30)

    @Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self.db_delete(channel.id)

    @Cog.listener()
    async def on_raw_thread_delete(self, event):
        await self.db_delete(event.thread_id)

    @loop(minutes=msg_interval)
    async def scheduler(self):
        await self.bot.wait_until_ready()
        await self.status('🔄')
        tasks = [
            self.collector(row) async for row in
            await self.db.select('channel', where='enabled = 1')
        ]
        if not len(tasks):
            return
        async with self.lock:
            for res in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(res, Exception):
                    await self.status('⚠')
                    raise res
        await self.status(None)

    async def cog_load(self):
        await self.db.start()
        self.scheduler.start()

    async def cog_unload(self):
        self.scheduler.cancel()
        self.workers.shutdown(wait=True, cancel_futures=True)
        await self.db.stop()

    async def cog_command_error(self, ctx: Context, error):
        if isinstance(error, CheckFailure):
            await ctx.message.add_reaction('🛑')
        elif isinstance(error, CommandError):
            import logging
            logging.getLogger(__package__).exception(error)
            await ctx.message.add_reaction('⚠')
        else:
            raise error

    async def status(self, message):
        await self.bot.change_presence(activity=discord.CustomActivity(message))

    async def db_update_insert(self, id, guild, enabled):
        async with self.db.transaction() as db:
            self.vecdb.get_or_create_collection(str(id), emb_metadata)
            await db.get_or_insert(
                'channel', Row(id=id, guild=guild),
            )
            await db.update(
                'channel', Row(enabled=enabled), 'id = :id', {'id': id},
            )

    async def db_delete(self, id):
        async with self.db.transaction() as db:
            if await db.get(
                'channel', where='id = :id', vals={'id': id},
            ) is not None:
                self.vecdb.delete_collection(str(id))
                await db.delete(
                    'channel', 'id = :id', {'id': id},
                )

    async def collector(self, row):
        now = utcnow()
        id, last = row['id'], row['lastsync']
        channel = self.bot.get_channel(id)
        it = channel.history(before=now, after=last)
        it = A.filterfalse(
            lambda msg: msg.author.bot or \
                self.bot.user in msg.mentions or \
                msg.content.startswith(BOT_PREFIX) or \
                msg.is_system(),
            it,
        )
        it = A.groupby(
            it,
            lambda msg: hash((msg.author.id, msg.created_at.timestamp() // 60)),
        )
        it = A.map(self.merge, it)
        it = as_chunks(it, msg_chunksize)
        async for chunk in it:
            await self.bot.loop.run_in_executor(self.workers, Worker.insert, id, chunk)
        async with self.db.transaction() as db:
            await db.update(
                'channel', Row(lastsync=now), 'id = :id', {'id': id},
            )

    async def merge(self, group):
        cat = lambda s: (s or '') + ' '
        clean = lambda s: re.sub(r'([^\w\-\.?]|_)+', ' ', s)
        async def _merge(acc, msg):
            content = ''
            for msg in self.replies(msg):
                content += cat(msg.clean_content)
                for att in msg.attachments:
                    content += cat(att.filename)
                for emb in msg.embeds:
                    content += cat(emb.title)
                    content += cat(emb.description)
            acc.setdefault('id', msg.id)
            acc.setdefault('at', msg.created_at)
            acc['content'] += clean(content)
            return acc
        return await A.reduce(_merge, group[1], Row(content=''))

    def replies(self, msg):
        if msg.reference is not None and \
            isinstance(msg.reference.resolved, discord.Message):
            yield from self.replies(msg.reference.resolved)
        yield msg

class Worker:

    local = threading.local()

    @classmethod
    def init(cls):
        cls.local.api = requests.Session()
        cls.local.db = chromadb.Client(
            chromadb.Settings(**vecdb_config),
        )
        cls.local.api.mount('', HTTPAdapter(Retry(
            status=8,
            total=None,
            backoff_factor=1,
            status_forcelist=(429,),
            allowed_methods=None,
            raise_on_status=True,
        )))

    @classmethod
    def embed(cls, it):
        return cls.local.api.post(
            EMBEDAPI,
            json=dict(
                inputs=tuple(it),
                normalize=True,
                truncate=True,
            ),
        ).json()

    @classmethod
    def insert(cls, id, batch):
        coll = cls.local.db.get_collection(str(id))
        coll.upsert(
            ids=list(str(msg['id']) for msg in batch),
            documents=list(msg['content'] for msg in batch),
            metadatas=list({'at': round(msg['at'].timestamp())} for msg in batch),
            embeddings=cls.embed(msg['content'] for msg in batch),
        )

    @classmethod
    def query(cls, id, query, num, where=None, where_doc=None):
        coll = cls.local.db.get_collection(str(id))
        return {k: v[0] for k, v in coll.query(
            query_embeddings=cls.embed((query,)),
            n_results=num,
            where=where,
            where_document=where_doc,
        ).items() if v is not None}

async def setup(bot):
    await bot.add_cog(FAQuery(bot))
