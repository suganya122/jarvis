import asyncio

from workers.common.displays import make_dpi_aware
from workers.common.server import serve
from workers.hands.server import make_server

if __name__ == "__main__":
    make_dpi_aware()  # Windows: real pixel coordinates on 125-200% displays
    asyncio.run(serve(make_server))
