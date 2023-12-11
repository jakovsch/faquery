import asyncio, discord, logging, uvloop
from discord.ext import commands

from .config import (
    DEBUG,
    BOT_API_TOKEN,
    BOT_PREFIX,
    BOT_DESCRIPTION,
    extensions,
)

__version__ = 0, 1, 0

config = dict(
    command_prefix=commands.when_mentioned_or(BOT_PREFIX),
    description=BOT_DESCRIPTION,
    intents=discord.Intents(
        guilds=True,
        messages=True,
        message_content=True,
    ),
)
perms = discord.Permissions(
    embed_links=True,
    attach_files=True,
    view_channel=True,
    add_reactions=True,
    manage_messages=True,
    manage_threads=True,
    read_messages=True,
    read_message_history=True,
    send_messages=True,
    send_messages_in_threads=True,
)

class Bot(commands.Bot):

    async def setup_hook(self):
        for ext in extensions:
            await self.load_extension(ext)

    async def on_ready(self):
        log = logging.getLogger(__package__)
        log.info(f'Debug: {DEBUG}')
        log.info(f'Version: {".".join(map(str, __version__))}')
        log.info(f'Invite URL: {discord.utils.oauth_url(self.user.id, permissions=perms)}')
        log.info(f'Bot user: {self.user.name} ({self.user.id})')

async def main():
    async with Bot(**config) as bot:
        discord.utils.setup_logging(
            level=logging.DEBUG if DEBUG else logging.INFO,
        )
        await bot.start(BOT_API_TOKEN)

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
asyncio.run(main(), debug=DEBUG)
