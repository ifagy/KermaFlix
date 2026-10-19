import asyncio
import logging
import os
from node import KermaNode

logging.basicConfig(
    level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s'
)


def main():
    port = int(os.environ.get('NODE_PORT', 18018))
    my_address = os.environ.get('MY_ADDRESS', 'kermanode.net:18018')

    node = KermaNode(host='0.0.0.0', port=port, my_address=my_address)

    try:
        asyncio.run(node.run())
    except KeyboardInterrupt:
        logging.info('Node gestoppt.')


if __name__ == '__main__':
    main()