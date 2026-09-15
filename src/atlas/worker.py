import asyncio
import signal

from atlas.db import pool


async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await pool.open(wait=True)
    await stop.wait()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
