"""Interactive OS secret storage. Keys never enter CLI arguments or project files."""
import argparse
import getpass
import keyring
import re

parser=argparse.ArgumentParser()
parser.add_argument('provider',choices=['openai','anthropic','brave','mail'])
parser.add_argument('--account-id',help='For mail only: the 32-character local account ID, never an address or password')
parser.add_argument('--delete',action='store_true')
args=parser.parse_args()
if args.provider=='mail':
    if not args.account_id or not re.fullmatch(r'[a-f0-9]{32}',args.account_id):parser.error('mail requires --account-id with a local account ID')
    reference='mail:'+args.account_id
else:
    if args.account_id:parser.error('--account-id is only valid for mail')
    reference=args.provider
backend=keyring.get_keyring()
if backend.priority<=0 or 'plaintext' in type(backend).__name__.lower():
    raise SystemExit('No secure operating-system credential store available.')
if args.delete:
    keyring.delete_password('Riwaq',reference)
else:
    value=getpass.getpass('Service credential (hidden): ')
    if not value:
        raise SystemExit('Empty key not saved.')
    keyring.set_password('Riwaq',reference,value)
print('Operating-system credential store updated.')
