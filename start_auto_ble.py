from pathlib import Path
import argparse
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Start Wio Terminal dashboard server with BLE auto-sync")
    parser.add_argument("--port", type=int, default=8765, help="HTTP server port")
    parser.add_argument("--device-name", default="Wio AI Quota", help="BLE device name")
    parser.add_argument("--interval", type=int, default=10, help="BLE send interval in seconds")
    args = parser.parse_args()

    try:
        import bleak  # noqa: F401
    except Exception:
        print("Warning: Python package 'bleak' is not installed.")
        print("Install it first:")
        print("  pip install -r requirements.txt")
        print()

    command = [
        sys.executable,
        str(ROOT / "server.py"),
        str(args.port),
        "--ble",
        "--ble-name",
        args.device_name,
        "--ble-interval",
        str(args.interval),
    ]

    print("Starting Wio Terminal dashboard server with BLE auto-sync ...")
    print(f"Project: {ROOT}")
    print(f"URL: http://127.0.0.1:{args.port}/preview.html")
    print(f"BLE device: {args.device_name}")
    print(f"BLE interval: {args.interval}s")
    print()

    subprocess.run(command, cwd=ROOT)


if __name__ == "__main__":
    main()
