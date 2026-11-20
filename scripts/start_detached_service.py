"""Launch a local service in its own session, independent of the tool shell."""
import argparse
from pathlib import Path
import subprocess  # nosec B404


def start_detached(command, log_path, cwd):
    with Path(log_path).open('ab', buffering=0) as log:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,  # nosec B603
                                   stdout=log, stderr=subprocess.STDOUT,
                                   close_fds=True, start_new_session=True)
    return process.pid


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error('Missing service command')
    print(start_detached(args.command, args.log, args.cwd))
