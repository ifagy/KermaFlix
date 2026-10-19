import socket
import threading

HOST = '127.0.0.1'
PORT = 18018


def listen_to_node(sock):
    """Liest laufend Nachrichten von der Node und druckt sie aus."""
    while True:
        try:
            data = sock.recv(4096)
            if not data:
                print('\n[SYSTEM] Node hat die Verbindung GESCHLOSSEN.')
                break
            print(f'\n[EMPFANGEN]: {data.decode("utf-8")}')
        except Exception:
            break


def main():
    s = socket.create_connection((HOST, PORT))
    print(f'Connected with Node on {HOST}:{PORT}')

    # Thread für den Empfang starten
    t = threading.Thread(target=listen_to_node, args=(s,), daemon=True)
    t.start()

    print('\nTippe dein JSON ein und drücke ENTER.')
    print("Tippe 'exit' zum Beenden.\n")

    while True:
        try:
            msg = input('DEINE EINGABE > ')
            if msg.strip().lower() == 'exit':
                break
            # Sendet die Eingabe IMMER mit einem \n am Ende
            s.sendall((msg + '\n').encode('utf-8'))
        except KeyboardInterrupt:
            break

    s.close()


if __name__ == '__main__':
    main()