import asyncio

from workers.browser.server import make_server
from workers.common.server import serve

if __name__ == "__main__":
    asyncio.run(serve(make_server))
