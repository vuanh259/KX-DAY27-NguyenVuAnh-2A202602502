"""Inspect bonuses: python -m bonuses calibration|history|fork."""
import argparse
import asyncio
import json
from dotenv import load_dotenv

from bonuses.tools import calibration, fork_checkpoint, history


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('calibration')
    hist = sub.add_parser('history')
    hist.add_argument('--thread', required=True)
    fork = sub.add_parser('fork')
    fork.add_argument('--thread', required=True)
    fork.add_argument('--checkpoint', required=True)
    fork.add_argument('--new-thread', required=True)
    args = parser.parse_args()
    if args.command == 'calibration':
        result = asyncio.run(calibration())
    elif args.command == 'history':
        result = asyncio.run(history(args.thread))
    else:
        result = asyncio.run(fork_checkpoint(args.thread, args.checkpoint, args.new_thread))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
