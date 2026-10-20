"""Entry point: reads the configuration from the environment and starts the node."""

import asyncio
import logging
import os
import signal

from node import KermaNode
from protocol import validate_peer_address

logging.basicConfig(
    level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s'
)


async def amain() -> None:
    """Configure and run the node until it receives SIGTERM or SIGINT.

    Environment variables:
      NODE_PORT   - TCP port to listen on (default 18018)
      MY_ADDRESS  - public 'host:port' advertised to other peers
    """
    port = int(os.environ.get('NODE_PORT', 18018))
    my_address = os.environ.get('MY_ADDRESS', 'kermanode.net:18018')

    if not validate_peer_address(my_address):
        logging.error(
            f'MY_ADDRESS "{my_address}" is not a valid peer address - '
            'node will not advertise itself in peers messages.'
        )
        my_address = None

    node = KermaNode(host='0.0.0.0', port=port, my_address=my_address)

    # Cancel the main task on SIGTERM/SIGINT for a clean shutdown.
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, main_task.cancel)
        except NotImplementedError:  # not supported on Windows
            pass

    try:
        await node.run()
    except asyncio.CancelledError:
        logging.info('Node stopped.')


if __name__ == '__main__':
    asyncio.run(amain())