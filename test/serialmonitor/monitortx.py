import struct
import sys
import threading
import time

import serial
import serial.tools.list_ports
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

BAUD_RATE = 115200
PAYLOAD_SIZE = 29

# Little Endian (<): uint16, uint8, uint8, 6x int16, 2x uint16, uint8, 3x int16, 1x uint16
STRUCT_FORMAT = "<H B B h h h h h h H H B h h h H"

last_timestamp_raw = 0
timestamp_rollover_offset = 0

console = Console()

# --- Globals for TX Command Handling ---
tx_queue = []
last_command_msg = "Awaiting input (type 's' or 'd' and press Enter)..."


def command_listener():
    """Background thread to listen for user input without blocking the dashboard."""
    global last_command_msg
    while True:
        try:
            cmd = input().strip().lower()
            if cmd == "s":
                tx_queue.append(bytes.fromhex("47 43 55 00"))
                last_command_msg = "[bold green]Sent: STOW (47 43 55 00)[/bold green]"
            elif cmd == "d":
                tx_queue.append(bytes.fromhex("47 43 AA 00"))
                last_command_msg = "[bold green]Sent: DEPLOY (47 43 AA 00)[/bold green]"
            elif cmd:
                last_command_msg = f"[bold red]Unknown command: {cmd}[/bold red]"
        except (EOFError, KeyboardInterrupt):
            break


def find_telemetry_port():
    ports = serial.tools.list_ports.comports()
    target_signatures = ["usbserial", "slab_usbtouart", "cp210", "ch340", "uart"]

    for port in ports:
        port_info = f"{port.device} {port.description}".lower()
        if any(sig in port_info for sig in target_signatures):
            return port.device

    for port in ports:
        if "bluetooth" not in port.device.lower() and "wlan" not in port.device.lower():
            return port.device

    return None


def create_dashboard(data=None, status="Initializing...", error=False):
    global last_command_msg

    if not data:
        table = Table(show_header=False, expand=True, box=None)
        table.add_row(Align.center(f"[bold {'red' if error else 'yellow'}]{status}[/]"))
        return Panel(
            table, title="[bold blue]Rocket Telemetry Downlink[/]", border_style="blue"
        )

    dynamics_table = Table(
        title="Flight Dynamics", expand=True, title_style="bold cyan"
    )
    dynamics_table.add_column("Sensor", justify="left", style="cyan", no_wrap=True)
    dynamics_table.add_column("X", justify="right", style="green")
    dynamics_table.add_column("Y", justify="right", style="green")
    dynamics_table.add_column("Z", justify="right", style="green")

    dynamics_table.add_row(
        "Accel (LSB)", str(data["ax"]), str(data["ay"]), str(data["az"])
    )
    dynamics_table.add_row(
        "Gyro (LSB)", str(data["gx"]), str(data["gy"]), str(data["gz"])
    )

    env_table = Table(
        title="Environment & Nav", expand=True, title_style="bold magenta"
    )
    env_table.add_column("Metric", justify="left", style="magenta")
    env_table.add_column("Value", justify="right", style="yellow")

    env_table.add_row("Altitude", f"{data['alt_m']} m")
    env_table.add_row("Pressure", f"{data['pressure_pa']} Pa")
    env_table.add_row("GPS Lat Offset", f"{data['lat_off']}")
    env_table.add_row("GPS Lon Offset", f"{data['lon_off']}")

    sys_table = Table(title="System State", expand=True, title_style="bold red")
    sys_table.add_column("Parameter", justify="left", style="red")
    sys_table.add_column("Status", justify="right", style="white")

    sys_table.add_row("Mission Time", f"{data['time_s']:.3f} s")
    sys_table.add_row("Packet ID", str(data["pkt_id"]))
    sys_table.add_row("Tribo ADC", str(data["tribo"]))
    sys_table.add_row("Battery Raw", str(data["batt"]))
    sys_table.add_row("KY-024 Analog", str(data["ky024"]))

    flags = []
    if data["launch"]:
        flags.append("[bold red]LAUNCH[/]")
    if data["apogee"]:
        flags.append("[bold yellow]APOGEE[/]")
    if data["chute"]:
        flags.append("[bold green]CHUTE[/]")
    flags_str = " | ".join(flags) if flags else "[dim]Pad Idle[/dim]"

    sys_table.add_row("Flight Phase", flags_str)

    cmd_table = Table(show_header=False, expand=True, box=None)
    cmd_table.add_row(Align.center(last_command_msg))

    layout = Layout()
    layout.split_column(
        Layout(name="telemetry", ratio=4), Layout(name="uplink", ratio=1)
    )

    layout["telemetry"].split_row(
        Layout(Panel(dynamics_table, border_style="cyan")),
        Layout(Panel(env_table, border_style="magenta")),
        Layout(Panel(sys_table, border_style="red")),
    )

    layout["uplink"].update(
        Panel(
            cmd_table,
            title="[bold yellow]Uplink Command Status[/]",
            border_style="yellow",
        )
    )

    return Panel(
        layout,
        title="[bold blue]Rocket Telemetry Downlink - LIVE[/]",
        border_style="green",
    )


def parse_payload(payload_bytes):
    global last_timestamp_raw, timestamp_rollover_offset

    unpacked = struct.unpack(STRUCT_FORMAT, payload_bytes)

    (
        t_raw,
        pkt_id,
        flags,
        ax,
        ay,
        az,
        gx,
        gy,
        gz,
        press_scaled,
        tribo,
        batt,
        lat_off,
        lon_off,
        alt_m,
        ky024,
    ) = unpacked

    if t_raw < last_timestamp_raw:
        timestamp_rollover_offset += 65536
    last_timestamp_raw = t_raw
    actual_time_ms = timestamp_rollover_offset + t_raw

    return {
        "time_s": actual_time_ms / 1000.0,
        "pkt_id": pkt_id,
        "launch": bool(flags & 0x01),
        "apogee": bool((flags & 0x02) >> 1),
        "chute": bool((flags & 0x04) >> 2),
        "ax": ax,
        "ay": ay,
        "az": az,
        "gx": gx,
        "gy": gy,
        "gz": gz,
        "pressure_pa": press_scaled * 2,
        "tribo": tribo,
        "batt": batt,
        "lat_off": lat_off,
        "lon_off": lon_off,
        "alt_m": alt_m,
        "ky024": ky024,
    }


def main():
    port = find_telemetry_port()
    if not port:
        console.print(
            create_dashboard(
                status="No valid serial port found. Check USB connection.", error=True
            )
        )
        sys.exit(1)

    try:
        # timeout=0 makes ser.read() non-blocking
        ser = serial.Serial(port, BAUD_RATE, timeout=0)
    except serial.SerialException as e:
        console.print(create_dashboard(status=f"Serial Error: {e}", error=True))
        sys.exit(1)

    cmd_thread = threading.Thread(target=command_listener, daemon=True)
    cmd_thread.start()

    rx_buffer = bytearray()
    last_rx_time = time.time()

    with Live(
        create_dashboard(status=f"Connected to {port}\nAwaiting telemetry..."),
        refresh_per_second=20,
        screen=True,
    ) as live:
        try:
            while True:
                # --- 1. Handle TX ---
                while tx_queue:
                    payload_out = tx_queue.pop(0)
                    ser.write(payload_out)
                    ser.flush()

                # --- 2. Handle RX (Non-Blocking) ---
                if ser.in_waiting > 0:
                    new_bytes = ser.read(ser.in_waiting)
                    now = time.time()

                    # Temporal Sync: If the line was silent for > 50ms,
                    # treat this as the definitive start of a brand new packet.
                    if now - last_rx_time > 0.05:
                        if len(rx_buffer) > 0 and len(rx_buffer) < PAYLOAD_SIZE:
                            # We caught partial garbage data. Clear it to perfectly align.
                            rx_buffer.clear()

                    rx_buffer.extend(new_bytes)
                    last_rx_time = now

                # --- 3. Process Full Packets ---
                while len(rx_buffer) >= PAYLOAD_SIZE:
                    payload = rx_buffer[:PAYLOAD_SIZE]
                    del rx_buffer[:PAYLOAD_SIZE]

                    try:
                        parsed_data = parse_payload(payload)
                        live.update(create_dashboard(parsed_data))
                    except struct.error:
                        live.update(
                            create_dashboard(
                                status="Struct unpack error! Alignment lost.",
                                error=True,
                            )
                        )
                        rx_buffer.clear()

                # Yield CPU to prevent 100% core saturation
                time.sleep(0.01)

        except KeyboardInterrupt:
            pass
        finally:
            ser.close()


if __name__ == "__main__":
    main()
