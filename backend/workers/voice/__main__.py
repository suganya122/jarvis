import asyncio

from workers.common.server import serve
from workers.voice.server import make_server

if __name__ == "__main__":
    asyncio.run(serve(make_server))
