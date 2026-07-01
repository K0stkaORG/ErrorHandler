#!/usr/bin/env python3
import socket
import serial
import threading
import time
import argparse
import sys

def parse_args():
    parser = argparse.ArgumentParser(description="Serial to TCP relay")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Serial port (default: /dev/ttyACM0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--tcp-port", type=int, default=8080, help="TCP port to listen on (default: 8080)")
    return parser.parse_args()

class RelayServer:
    def __init__(self, serial_port, baud_rate, tcp_port):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.tcp_port = tcp_port
        
        self.ser = None
        self.client_socket = None
        self.client_address = None
        self.lock = threading.Lock()
        
        self.running = True

    def start(self):
        self.tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_server.bind(('0.0.0.0', self.tcp_port))
        self.tcp_server.listen(5)
        print(f"Listening for TCP connections on 0.0.0.0:{self.tcp_port}")

        serial_thread = threading.Thread(target=self.serial_loop, daemon=True)
        serial_thread.start()

        tcp_thread = threading.Thread(target=self.tcp_accept_loop, daemon=True)
        tcp_thread.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down...")
            self.running = False
            if self.ser:
                self.ser.close()
            if self.client_socket:
                self.client_socket.close()
            self.tcp_server.close()
            sys.exit(0)

    def tcp_accept_loop(self):
        while self.running:
            try:
                client_sock, client_addr = self.tcp_server.accept()
                with self.lock:
                    if self.client_socket:
                        print(f"Dropping existing connection from {self.client_address}")
                        try:
                            self.client_socket.close()
                        except:
                            pass
                    
                    self.client_socket = client_sock
                    self.client_address = client_addr
                    print(f"Accepted new connection from {self.client_address}")
                
                # Start a thread to read from this TCP client
                client_thread = threading.Thread(target=self.tcp_read_loop, args=(client_sock, client_addr), daemon=True)
                client_thread.start()
            except Exception as e:
                if self.running:
                    print(f"Error accepting TCP connection: {e}")
                time.sleep(1)

    def tcp_read_loop(self, sock, addr):
        while self.running:
            try:
                data = sock.recv(4096)
                if not data:
                    print(f"Client {addr} disconnected.")
                    break
                
                with self.lock:
                    if self.ser and self.ser.is_open:
                        self.ser.write(data)
                        self.ser.flush()
                        
            except Exception as e:
                print(f"TCP read error from {addr}: {e}")
                break
                
        with self.lock:
            if self.client_socket == sock:
                self.client_socket = None
                self.client_address = None
        try:
            sock.close()
        except:
            pass

    def serial_loop(self):
        while self.running:
            try:
                if self.ser is None or not self.ser.is_open:
                    print(f"Attempting to connect to serial port {self.serial_port} at {self.baud_rate}...")
                    self.ser = serial.Serial(self.serial_port, self.baud_rate, timeout=1)
                    # For ESP boards, sometimes toggling DTR/RTS or clearing buffers helps
                    self.ser.dtr = False
                    self.ser.rts = False
                    self.ser.reset_input_buffer()
                    self.ser.reset_output_buffer()
                    print(f"Successfully connected to {self.serial_port}")

                data = self.ser.read(4096)
                if data:
                    with self.lock:
                        if self.client_socket:
                            try:
                                self.client_socket.sendall(data)
                            except Exception as e:
                                print(f"Error sending to TCP client: {e}")
                                self.client_socket.close()
                                self.client_socket = None
                                self.client_address = None
            except serial.SerialException as e:
                print(f"Serial port error: {e}")
                if self.ser:
                    try:
                        self.ser.close()
                    except:
                        pass
                self.ser = None
                time.sleep(2) # Wait before reconnecting
            except Exception as e:
                print(f"Unexpected serial loop error: {e}")
                time.sleep(2)

if __name__ == "__main__":
    args = parse_args()
    relay = RelayServer(args.port, args.baud, args.tcp_port)
    relay.start()
