"""Check the connection to px-seracbio-dev over the VPN.

Credentials come from ~/.px_db_dev_password, one line "user:password", shared through 1Password.
Use --prompt to type the password instead, so nothing is stored on disk. The script makes no AWS
call, so a person without an AWS account can run it. They need the VPN and global-bundle.pem.
"""

import argparse
import getpass
from pathlib import Path

import psycopg2

HOST = 'px-seracbio-dev.cfyi0icu0fkt.eu-north-1.rds.amazonaws.com'
DATABASE = 'postgres'
CRED_FILE = Path.home() / '.px_db_dev_password'
CA_BUNDLE = Path(__file__).resolve().parents[1] / 'global-bundle.pem'

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--prompt', action='store_true',
                    help='ask for the password instead of reading the credential file')
parser.add_argument('--user', default='seracbio', help='database user for --prompt')
args = parser.parse_args()

if args.prompt or not CRED_FILE.exists():
    user = args.user
    password = getpass.getpass(f'password for {user}: ')
    source = 'typed, not stored'
else:
    user, password = CRED_FILE.read_text().strip().split(':', 1)
    source = str(CRED_FILE)

print(f'host: {HOST}\nuser: {user}\npassword from: {source}')

conn = None
try:
    conn = psycopg2.connect(host=HOST, port=5432, database=DATABASE, user=user,
                            password=password, sslmode='verify-full',
                            sslrootcert=str(CA_BUNDLE))
    with conn.cursor() as cur:
        cur.execute('SELECT version();')
        print(cur.fetchone()[0])
        cur.execute('SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1;')
        print('databases:', [row[0] for row in cur.fetchall()])
        cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN "
                    "('pg_catalog', 'information_schema');")
        print('user tables:', cur.fetchone()[0])
except Exception as error:
    print(f'Database error: {error}')
    raise
finally:
    if conn:
        conn.close()
