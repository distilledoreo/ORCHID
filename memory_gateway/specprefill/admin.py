"""Manage the lean gateway's stored threads from the command line (talks to the running gateway).

  list                     threads, newest first: turns, sizes, a preview
  export THREAD [-o FILE]  the thread's messages as JSON lines
  delete THREAD [--yes]    remove a thread completely (asks first)
  purge-caches             drop parked states and session files (rebuilt on demand)
  storage                  bytes held, quota and warning

Usage: python -m memory_gateway.specprefill.admin [--url URL] COMMAND ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default=os.environ.get('ORCHID_GATEWAY_URL', 'http://127.0.0.1:18080'))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    export = commands.add_parser('export')
    export.add_argument('thread')
    export.add_argument('-o', '--output')
    delete = commands.add_parser('delete')
    delete.add_argument('thread')
    delete.add_argument('--yes', action='store_true', help='do not ask for confirmation')
    commands.add_parser('purge-caches')
    commands.add_parser('storage')
    args = parser.parse_args(argv)
    client = httpx.Client(base_url=args.url.rstrip('/'), timeout=600)

    def check(response):
        if response.status_code != 200:
            sys.exit(f'{response.status_code}: {response.text[:300]}')
        return response
    if args.command == 'list':
        rows = check(client.get('/v1/orchid/threads')).json()['data']
        for row in rows:
            print(f"{row['thread'][:16]}  {row['turns']:>5} turns  {row['archive_bytes'] / 2**20:8.1f} MiB  "
                  f"cache {row['cache_bytes'] / 2**20:8.1f} MiB  {row.get('preview') or ''!r:.60}")
        if not rows:
            print('no threads')
    elif args.command == 'export':
        response = check(client.get(f'/v1/orchid/threads/{args.thread}/export'))
        if args.output:
            with open(args.output, 'w') as f:
                f.write(response.text)
        else:
            sys.stdout.write(response.text)
    elif args.command == 'delete':
        if not args.yes and input(f'Delete thread {args.thread} and everything stored for it? [y/N] ').lower() != 'y':
            sys.exit('not deleted')
        print(json.dumps(check(client.delete(f'/v1/orchid/threads/{args.thread}')).json()))
    elif args.command == 'purge-caches':
        print(json.dumps(check(client.post('/v1/orchid/caches/purge')).json()))
    else:
        print(json.dumps(check(client.get('/v1/orchid/storage')).json(), indent=1))


if __name__ == '__main__':
    main()
