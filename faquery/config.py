import os, dotenv, pathlib

dotenv.load_dotenv(dotenv_path=pathlib.Path('.env'))

BOT_API_TOKEN = os.getenv('BOT_API_TOKEN', '')
BOT_PREFIX = os.getenv('BOT_PREFIX', '?')
BOT_DESCRIPTION = os.getenv('BOT_DESCRIPTION', '<todo>')
DEBUG = int(os.getenv('DEBUG', '0'))
DB = os.getenv('DB', '/data/bot.db')
VECDB = os.getenv('VECDB', 'db:8000').split(':')
EMBEDAPI = f'http://{os.getenv("EMBEDAPI", "api:80")}/embed'

vecdb_config = dict(
    chroma_server_host=VECDB[0],
    chroma_server_http_port=VECDB[1],
    chroma_server_ssl_enabled=False,
    chroma_server_headers=None,
    chroma_api_impl='chromadb.api.fastapi.FastAPI',
    anonymized_telemetry=False,
)

extensions = map(
    lambda fname: f'{__package__}.ext.{fname.removesuffix(".py")}',
    os.listdir(os.path.join(os.path.dirname(__file__), 'ext')),
)

db_poolsize = 4
msg_interval = 60
msg_chunksize = 128
emb_metadata = {'hnsw:space': 'cosine'}
