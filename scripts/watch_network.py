"""Run the school server until stopped, recovering from an unexpected process exit."""
import signal
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from run_network import configured_address


def supervise(command,pinned_address=None):
    child=None
    stopping=False
    def stop(signum,frame):
        nonlocal stopping
        stopping=True
        if child and child.poll() is None:child.terminate()
    previous={sig:signal.signal(sig,stop) for sig in (signal.SIGINT,signal.SIGTERM)}
    failures=0
    try:
        while not stopping:
            started=time.monotonic()
            environment=None
            if pinned_address is not None:environment={**os.environ,'_IDENTIQ_BIND_ADDRESS':pinned_address}
            child=subprocess.Popen(command,env=environment)
            code=child.wait()
            if stopping or code==0:return code
            # A startup failure is usually an invalid address, occupied port or bad config.
            failures=failures+1 if time.monotonic()-started<10 else 0
            if failures>=3:
                print('Server could not start. Fix the address or port shown above, then run it again.',file=sys.stderr,flush=True)
                return code
            delay=min(15,2**(failures+1))
            print(f'Server stopped unexpectedly. Restarting in {delay} seconds…',flush=True)
            until=time.monotonic()+delay
            while not stopping and time.monotonic()<until:time.sleep(.1)
        return 0
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        for sig,handler in previous.items():signal.signal(sig,handler)


def main():
    arguments=sys.argv[1:]
    command=[sys.executable,str(Path(__file__).with_name('run_network.py')),'--worker',*arguments]
    if '--help' in arguments or '-h' in arguments:return subprocess.call(command)
    parser=argparse.ArgumentParser(add_help=False)
    choices=parser.add_mutually_exclusive_group()
    choices.add_argument('--address');choices.add_argument('--auto-address',action='store_true')
    options,_=parser.parse_known_args(arguments)
    # Keep this run on its approved interface without saving automatically discovered IPs.
    address=configured_address(options.address,auto=options.auto_address)
    return supervise(command,pinned_address=address)


if __name__=='__main__':
    try:sys.exit(main())
    except ValueError as error:print(str(error),file=sys.stderr);sys.exit(1)
