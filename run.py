import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from app.server import ButlerApplication, ButlerHTTPServer


ROOT = Path(__file__).resolve().parent


def run_tests() -> int:
    commands = [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]]
    node = shutil.which("node")
    if node:
        commands.extend([[node, "--check", "static/app.js"], [node, "--check", "static/art.mjs"], [node, "--test", "tests/test_ui.mjs"]])
    else:
        print("Node is not installed; JavaScript checks are unverified.", flush=True)
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Butler local prototype")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--test", action="store_true")
    arguments = parser.parse_args()
    if arguments.test:
        return run_tests()
    if not 1024 <= arguments.port <= 65525:
        parser.error("Choose a port between 1024 and 65525.")
    app = ButlerApplication(arguments.data_dir)
    server = None
    for port in range(arguments.port, arguments.port + 10):
        try:
            server = ButlerHTTPServer(("127.0.0.1", port), app)
            break
        except OSError as error:
            if error.errno not in (48, 98, 10048):
                app.shutdown()
                raise
    if server is None:
        app.shutdown()
        print("No free port found. Run again with --port and a different port.", file=sys.stderr)
        return 1
    print(f"Butler is ready at http://127.0.0.1:{server.server_port}", flush=True)
    print(f"Assistant: {app.capabilities()['assistant']} | Catalog: fictional samples | Media tools: {'ready' if app.tools.status()['ready'] else 'not configured'}", flush=True)
    print("Local access only. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())