"""Run a query against px-seracbio-prod and return a pandas DataFrame.

Credentials come from ~/.px_db_password, one line "user:password", shared through 1Password.
Use --prompt to type the password instead, so nothing is stored on disk. Needs the VPN.
"""

import argparse
import getpass
from pathlib import Path

import pandas as pd
import psycopg2

HOST = 'px-seracbio-prod.cfyi0icu0fkt.eu-north-1.rds.amazonaws.com'
DATABASE = 'postgres'
CRED_FILE = Path.home() / '.px_db_password'
CA_BUNDLE = Path(__file__).resolve().parents[1] / 'global-bundle.pem'

QUERY = """
SELECT DISTINCT uniquecontrast, plate
FROM public.px_fbx_measure
ORDER BY plate, uniquecontrast
"""


def credentials(prompt, user):
    """Return (user, password) from the credential file, or typed when it is absent."""
    if prompt or not CRED_FILE.exists():
        return user, getpass.getpass(f'password for {user}: ')
    return CRED_FILE.read_text().strip().split(':', 1)


def query_df(sql, user, password):
    """Run sql and return the result as a DataFrame, with the column names from the cursor."""
    with psycopg2.connect(host=HOST, port=5432, database=DATABASE, user=user,
                          password=password, sslmode='verify-full',
                          sslrootcert=str(CA_BUNDLE)) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompt', action='store_true',
                        help='ask for the password instead of reading the credential file')
    parser.add_argument('--user', default='seracbio', help='database user for --prompt')
    parser.add_argument('--out', help='optional path to write the result as CSV')
    args = parser.parse_args()

    df = query_df(QUERY, *credentials(args.prompt, args.user))
    print(f'rows: {len(df):,} | columns: {list(df.columns)}')
    print(df.head(10))
    if args.out:
        df.to_csv(args.out, index=False)
        print(f'written: {args.out}')
