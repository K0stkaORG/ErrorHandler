#!/usr/bin/env python3
import socket
import threading
import sys
import argparse

def parse_args():
    parser = argparse.ArgumentParser(description="TCP Client to connect to relay.py")
    parser.add_argument("--host", default="127.0.0.1", help="Host IP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="TCP port (default: 8080)")
    return parser.parse_args()

def receive_loop(sock):
    while True:
        try:
            data = sock.recv(4096)
            if not data:
                print("\nServer closed the connection.")
                # Exit the program when server disconnects
                import os
                os._exit(0)
            
            # Convert bytes to hex string
            hex_data = " ".join(f"{b:02X}" for b in data)
            # Clear the current line (assuming prompt is there) and print RX data
            sys.stdout.write(f"\rRX: {hex_data}\n> ")
            sys.stdout.flush()
        except Exception as e:
            print(f"\nError receiving data: {e}")
            import os
            os._exit(1)

def main():
    args = parse_args()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        print(f"Connecting to {args.host}:{args.port}...")
        sock.connect((args.host, args.port))
        print("Connected successfully!")
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)

    # Start the receive thread
    rx_thread = threading.Thread(target=receive_loop, args=(sock,), daemon=True)
    rx_thread.start()

    print("Enter bytes in hex format (e.g., '0A 1B 2C' or '0a1b2c') to send.")
    print("Type 'exit' or 'quit' to close.")

    try:
        while True:
            # Prompt the user
            user_input = input("> ").strip()
            
            if user_input.lower() in ('exit', 'quit'):
                break
            
            if not user_input:
                continue

            # Clean up the input string (remove spaces, commas, etc.)
            clean_input = "".join(c for c in user_input if c.isalnum())
            
            try:
                # Convert the hex string back to bytes
                data_to_send = bytes.fromhex(clean_input)
                sock.sendall(data_to_send)
                print(f"TX: {' '.join(f'{b:02X}' for b in data_to_send)}")
            except ValueError:
                print("Invalid hex string. Please enter valid hex pairs.")
            except Exception as e:
                print(f"Failed to send data: {e}")
                break

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        sock.close()

if __name__ == "__main__":
    main()
