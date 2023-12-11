import asyncio, asyncstdlib as A, aiosqlite, contextlib
from collections import OrderedDict
from datetime import datetime
from sqlite3 import PARSE_COLNAMES, PARSE_DECLTYPES

from .config import DB, DEBUG

def quote(s):
    return (
        s if s.startswith('"') and s.endswith('"')
        else '"' + s.replace('"', '""') + '"'
    )

async def amaybe(it):
    return await A.anext(it, None)

class Row(OrderedDict):

    @classmethod
    def factory(cls, cursor, row):
        fields = (col[0] for col in cursor.description)
        return cls(zip(fields, row))

class Transaction:

    def __init__(self, conn: aiosqlite.Connection):
        self.db = conn

    async def run(self, query, vals={}):
        async with self.db.execute(query, vals) as cursor:
            async for row in cursor: yield row

    async def select(self, table, cols=[], where='', vals={}):
        where_s = f'WHERE {where}' if where else ''
        cols_s = ', '.join(map(quote, cols)) if cols else '*'
        return self.run(
            f'SELECT {cols_s} FROM {quote(table)} {where_s}',
            vals,
        )

    async def insert(self, table, row):
        keys = tuple(row.keys())
        vals = tuple(row.values())
        ph = ('?,' * len(keys))[:-1]
        cols_s = ', '.join(map(quote, keys))
        rowid = await self.db.execute_insert(
            f'INSERT INTO {quote(table)} ({cols_s})\nVALUES ({ph});',
            vals,
        )
        return await self.get(
            table, where='rowid = :id',
            vals={'id': rowid['last_insert_rowid()']},
        )

    async def delete(self, table, where, vals={}):
        return await amaybe(self.run(
            f'DELETE FROM {quote(table)} WHERE {where}',
            vals,
        ))

    async def update(self, table, row, where='', vals={}):
        where_s = f'WHERE {where}' if where else ''
        upd_s = ', '.join(f'{quote(key)} = :{key}' for key in row.keys())
        return await amaybe(self.run(
            f'UPDATE {quote(table)} SET {upd_s} {where_s}',
            {**row, **vals},
        ))

    async def get(self, table, cols=[], where='', vals={}):
        rows = await self.select(table, cols, where, vals)
        return await amaybe(rows)

    async def get_or_insert(self, table, row, where=''):
        where_s = where or ' AND '.join(f'{quote(key)} = :{key}' for key in row.keys())
        erow = await self.get(table, where=where_s, vals=row)
        return erow or await self.insert(table, row)

class Database:

    schema = '''
    CREATE TABLE IF NOT EXISTS "channel"
    (
        id          INTEGER NOT NULL PRIMARY KEY,
        guild       INTEGER NOT NULL,
        enabled     BOOLEAN NOT NULL DEFAULT 1,
        lastsync    TIMESTAMP NOT NULL DEFAULT 1420070400
    );
    '''

    def __init__(self, pool_size=4):
        aiosqlite.register_adapter(datetime, lambda dt: round(dt.timestamp()))
        aiosqlite.register_converter('TIMESTAMP', lambda b: datetime.fromtimestamp(int(b)))
        aiosqlite.register_adapter(bool, lambda b: int(b))
        aiosqlite.register_converter('BOOLEAN', lambda b: bool(int(b)))
        self.pool_size = pool_size
        self.pool = asyncio.Queue(pool_size)
        self.default = None

    async def start(self):
        for _ in range(self.pool_size):
            conn = await aiosqlite.connect(
                DB,
                isolation_level=None,
                detect_types=PARSE_COLNAMES | PARSE_DECLTYPES,
            )
            conn.row_factory = Row.factory
            await self.pool.put(conn)
        self.default = Transaction(await self.pool.get())
        await self.default.db.executescript(Database.schema)
        return self

    async def stop(self):
        await self.pool.put(self.default.db)
        for _ in range(self.pool_size):
            conn = await self.pool.get()
            await conn.close()

    async def __await__(self):
        return self.start().__await__()

    async def __aenter__(self):
        return await self

    async def __aexit__(self, *_):
        await self.stop()

    @contextlib.asynccontextmanager
    async def transaction(self):
        try:
            conn = await self.pool.get()
            await conn.execute('BEGIN')
            yield Transaction(conn)
        except:
            await conn.rollback()
            raise
        else:
            await conn.commit()
        finally:
            await self.pool.put(conn)

    def __getattr__(self, attr):
        return getattr(self.default, attr)
