import serial
import serial.tools.list_ports
import struct
import sys
import time
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.console import Console
from rich.align import Align

BAUD_RATE = 115200
PAYLOAD_SIZE = 27

# Little Endian (<): uint16, uint8, uint8, 6x int16, 2x uint16, uint8, 3x int16
STRUCT_FORMAT = '<H B B h h h h h h H H B h h h'

last_timestamp_raw = 0
timestamp_rollover_offset = 0

console = Console()

def find_telemetry_port():
    """Automatically locates the ESP32 serial port on macOS/Linux/Windows."""
    ports = serial.tools.list_ports.comports()
    
    # Common signatures for ESP32/LilyGO boards
    target_signatures = ['usbserial', 'slab_usbtouart', 'cp210', 'ch340', 'uart']
    
    for port in ports:
        port_info = f"{port.device} {port.description}".lower()
        if any(sig in port_info for sig in target_signatures):
            return port.device
            
    # Fallback if specific signatures aren't found, grab the first non-Bluetooth port
    for port in ports:
        if 'bluetooth' not in port.device.lower() and 'wlan' not in port.device.lower():
            return port.device
            
    return None

def create_dashboard(data=None, status="Initializing...", error=False):
    """Generates the Rich layout for the CLI dashboard."""
    if not data:
        table = Table(show_header=False, expand=True, box=None)
        table.add_row(Align.center(f"[bold {'red' if error else 'yellow'}]{status}[/]"))
        return Panel(table, title="[bold blue]Rocket Telemetry Downlink[/]", border_style="blue")

    # --- Flight Dynamics Table ---
    dynamics_table = Table(title="Flight Dynamics", expand=True, title_style="bold cyan")
    dynamics_table.add_column("Sensor", justify="left", style="cyan", no_wrap=True)
    dynamics_table.add_column("X", justify="right", style="green")
    dynamics_table.add_column("Y", justify="right", style="green")
    dynamics_table.add_column("Z", justify="right", style="green")
    
    dynamics_table.add_row("Accel (LSB)", str(data['ax']), str(data['ay']), str(data['az']))
    dynamics_table.add_row("Gyro (LSB)", str(data['gx']), str(data['gy']), str(data['gz']))

    # --- Environment & Navigation Table ---
    env_table = Table(title="Environment & Nav", expand=True, title_style="bold magenta")
    env_table.add_column("Metric", justify="left", style="magenta")
    env_table.add_column("Value", justify="right", style="yellow")
    
    env_table.add_row("Altitude", f"{data['alt_m']} m")
    env_table.add_row("Pressure", f"{data['pressure_pa']} Pa")
    env_table.add_row("GPS Lat Offset", f"{data['lat_off']}")
    env_table.add_row("GPS Lon Offset", f"{data['lon_off']}")

    # --- System & Mission State Table ---
    sys_table = Table(title="System State", expand=True, title_style="bold red")
    sys_table.add_column("Parameter", justify="left", style="red")
    sys_table.add_column("Status", justify="right", style="white")
    
    sys_table.add_row("Mission Time", f"{data['time_s']:.3f} s")
    sys_table.add_row("Packet ID", str(data['pkt_id']))
    sys_table.add_row("Tribo ADC", str(data['tribo']))
    sys_table.add_row("Battery Raw", str(data['batt']))
    
    flags = []
    if data['launch']: flags.append("[bold red]LAUNCH[/]")
    if data['apogee']: flags.append("[bold yellow]APOGEE[/]")
    if data['chute']: flags.append("[bold green]CHUTE[/]")
    flags_str = " | ".join(flags) if flags else "[dim]Pad Idle[/dim]"
    
    sys_table.add_row("Flight Phase", flags_str)

    layout = Layout()
    layout.split_row(
        Layout(Panel(dynamics_table, border_style="cyan")),
        Layout(Panel(env_table, border_style="magenta")),
        Layout(Panel(sys_table, border_style="red"))
    )
    
    return Panel(layout, title="[bold blue]Rocket Telemetry Downlink - LIVE[/]", border_style="green")

def parse_payload(payload_bytes):
    """Unpacks the binary payload and applies scaling."""
    global last_timestamp_raw, timestamp_rollover_offset
    
    unpacked = struct.unpack(STRUCT_FORMAT, payload_bytes)
    
    (t_raw, pkt_id, flags, 
     ax, ay, az, gx, gy, gz, 
     press_scaled, tribo, batt, 
     lat_off, lon_off, alt_m) = unpacked

    if t_raw < last_timestamp_raw:
        timestamp_rollover_offset += 65536
    last_timestamp_raw = t_raw
    actual_time_ms = timestamp_rollover_offset + t_raw

    return {
        'time_s': actual_time_ms / 1000.0,
        'pkt_id': pkt_id,
        'launch': bool(flags & 0x01),
        'apogee': bool((flags & 0x02) >> 1),
        'chute': bool((flags & 0x04) >> 2),
        'ax': ax, 'ay': ay, 'az': az,
        'gx': gx, 'gy': gy, 'gz': gz,
        'pressure_pa': press_scaled * 2,
        'tribo': tribo,
        'batt': batt,
        'lat_off': lat_off,
        'lon_off': lon_off,
        'alt_m': alt_m
    }

def align_serial_stream(ser):
    """Uses temporal framing to find the 200ms gap between packets."""
    ser.reset_input_buffer()
    # A 50ms timeout is short enough to detect the 197ms dead air
    ser.timeout = 0.05 
    
    while True:
        # Read until the line goes silent (timeout occurs)
        if not ser.read(1):
            break 
            
    # We are now perfectly in the gap. Next byte is byte 0 of the packet.
    ser.timeout = 0.5 # Increase timeout so we don't timeout while reading the payload
    return True

def main():
    port = find_telemetry_port()
    if not port:
        console.print(create_dashboard(status="No valid serial port found. Check USB connection.", error=True))
        sys.exit(1)

    try:
        ser = serial.Serial(port, BAUD_RATE)
    except serial.SerialException as e:
        console.print(create_dashboard(status=f"Serial Error: {e}", error=True))
        sys.exit(1)

    with Live(create_dashboard(status=f"Connected to {port}\nAligning to temporal frame..."), refresh_per_second=10, screen=True) as live:
        try:
            align_serial_stream(ser)
            
            while True:
                payload = ser.read(PAYLOAD_SIZE)
                
                if len(payload) == PAYLOAD_SIZE:
                    parsed_data = parse_payload(payload)
                    live.update(create_dashboard(parsed_data))
                else:
                    # If we slipped out of alignment (dropped byte), force a temporal re-sync
                    live.update(create_dashboard(status="Byte slip detected! Re-aligning stream...", error=True))
                    align_serial_stream(ser)
                    
        except KeyboardInterrupt:
            pass
        finally:
            ser.close()

if __name__ == '__main__':
    main()
