"""Command-line access to the Px PostgreSQL RDS (Terraform stack: aws-rds/).

The database is private. Connect from the office LAN or over FortiClient. Every command needs a login:
  - personal: --user NAME (or RDS_USER). The script asks for the password. With --host (or RDS_HOST)
    too, it needs no AWS access. Colleagues use this login.
  - master: --admin. The script reads the endpoint and the master password from AWS at run time
    (Secrets Manager), so your AWS keys must allow it. create-demo and create-user need it.
Without a login the script stops; it never falls back to the master. Paths are relative to the repo root.

    python python/px_rds.py check --admin               # endpoint, DNS, TCP, TLS + SQL, one line each
    python python/px_rds.py pull px_demo --user me      # save a table to RDS_PULL_DIR/<table>.parquet
    python python/px_rds.py set-password --user me      # change your own password
    python python/px_rds.py create-demo --admin         # replace RDS_DEMO_TABLE with synthetic rows
    python python/px_rds.py create-user alice --admin   # login role with read + write rights
"""
import os
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_REPO_ROOT)

import json, socket, getpass, argparse
from contextlib import closing
import boto3
import numpy as np
import pandas as pd
import psycopg2
import yaml
from psycopg2 import sql
from psycopg2.extensions import encrypt_password
from psycopg2.extras import execute_values


# ~~~~~~~~~~~~~~~~~~~~~~
# FUNCTIONS
# ~~~~~~~~~~~~~~~~~~~~~~

def load_config(path):
    """Return the YAML config as a dict."""
    with open(path) as f:
        return yaml.safe_load(f)


def describe_instance(cfg):
    """Return the RDS instance description, read live from the AWS API."""
    rds = boto3.client("rds", region_name=cfg.get("RDS_REGION", "eu-north-1"))
    return rds.describe_db_instances(DBInstanceIdentifier=cfg.get("RDS_INSTANCE_ID", "px-rds"))["DBInstances"][0]


def rds_credentials(cfg, secret_arn):
    """Return (user, password) from the Secrets Manager secret that RDS manages. Nothing goes to disk."""
    sm = boto3.client("secretsmanager", region_name=cfg.get("RDS_REGION", "eu-north-1"))
    secret = json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])
    return secret["username"], secret["password"]


def endpoint(cfg):
    """Return (host, port): RDS_HOST from the config, or a live lookup in the AWS API."""
    if cfg.get("RDS_HOST"):
        return cfg["RDS_HOST"], 5432   # the PostgreSQL port that aws-rds/ uses
    db = describe_instance(cfg)
    return db["Endpoint"]["Address"], db["Endpoint"]["Port"]


def login(cfg, admin=False):
    """Return (user, password): the master user from Secrets Manager with `admin`, else RDS_USER and a hidden
    password prompt. With neither, stop: the script never falls back to the master login."""
    if admin:
        return rds_credentials(cfg, describe_instance(cfg)["MasterUserSecret"]["SecretArn"])
    if not cfg.get("RDS_USER"):
        raise SystemExit("no login: pass --user NAME (or set RDS_USER); the master login needs --admin")
    return cfg["RDS_USER"], getpass.getpass(f"password for {cfg['RDS_USER']}: ")


def open_db(cfg, user, password, host, port):
    """Connect as `user` and verify the server certificate and host name (sslmode=verify-full)."""
    return psycopg2.connect(host=host, port=port, dbname=cfg.get("RDS_DB_NAME", "px"), user=user,
                            password=password, sslmode="verify-full",
                            sslrootcert=cfg.get("RDS_SSLROOTCERT", "global-bundle.pem"), connect_timeout=10)


def connect(cfg, admin=False):
    """Log in first (see `login`), then open a verified connection to the endpoint."""
    return open_db(cfg, *login(cfg, admin), *endpoint(cfg))


def pg_type(dtype):
    """Return the PostgreSQL column type for a pandas dtype: bool, int and float map; all else is TEXT."""
    return {"b": "BOOLEAN", "i": "BIGINT", "u": "BIGINT", "f": "DOUBLE PRECISION"}.get(dtype.kind, "TEXT")


def write_frame(conn, table, df):
    """Replace `table` with the rows of `df` in one transaction. The column types follow the dtypes."""
    cols = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(pg_type(t)))
                              for c, t in df.dtypes.items())
    with conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {t}; CREATE TABLE {t} ({c})").format(t=sql.Identifier(table), c=cols))
        execute_values(cur, sql.SQL("INSERT INTO {} VALUES %s").format(sql.Identifier(table)),
                       list(zip(*(df[c].tolist() for c in df))), page_size=1000)


def read_table(conn, table):
    """Return the whole table as a DataFrame."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def make_demo_frame(n_rows, seed):
    """Return a synthetic frame with the slim `meas` schema: fake C_/G_/P_ ids, random logfc and pvalue."""
    rng = np.random.default_rng(seed)
    logfc, pvalue = rng.normal(0, 1, n_rows), rng.uniform(1e-6, 1, n_rows)
    return pd.DataFrame({"uniquecontrast": [f"C_{i:03d}" for i in rng.integers(0, 20, n_rows)],
                         "genes": [f"G_{i:04d}" for i in rng.integers(0, 500, n_rows)],
                         "plate": [f"P_{i:02d}" for i in rng.integers(0, 5, n_rows)],
                         "logfc": logfc,
                         "pvalue": pvalue,
                         "significant": ((pvalue < 0.05) & (np.abs(logfc) > 1)).astype("int64")})


def check(cfg, admin=False):
    """Test each link to the database in order, and print one line for each: endpoint, DNS, TCP, TLS + SQL."""
    user, password = login(cfg, admin)
    host, port = endpoint(cfg)
    print(f"host {host}:{port} (from {'RDS_HOST' if cfg.get('RDS_HOST') else 'the AWS API'})")
    ip = socket.gethostbyname(host)
    print(f"dns  {ip}")
    socket.create_connection((ip, port), timeout=5).close()
    print(f"tcp  {ip}:{port} open")
    with closing(open_db(cfg, user, password, host, port)) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user, version, current_setting('server_version') FROM pg_stat_ssl "
                    "WHERE pid = pg_backend_pid()")
        user, tls, server = cur.fetchone()
    print(f"sql  logged in as {user} over {tls} to PostgreSQL {server}, server certificate verified")


def create_demo(cfg):
    """Write the synthetic table, read it back, and check that the round trip keeps every value."""
    df = make_demo_frame(cfg.get("RDS_DEMO_ROWS", 1000), cfg.get("RDS_DEMO_SEED", 0))
    table = cfg.get("RDS_DEMO_TABLE", "px_demo")
    with closing(connect(cfg, admin=True)) as conn:
        write_frame(conn, table, df)
        back = read_table(conn, table)
    key = list(df.columns)
    pd.testing.assert_frame_equal(back.sort_values(key, ignore_index=True), df.sort_values(key, ignore_index=True))
    print(f"{table}: wrote {len(df)} rows x {df.shape[1]} columns, read them back, all values identical")


def pull(cfg, table, out=None, admin=False):
    """Save `table` to a local .parquet or .csv file, then print it with its shape and dtypes."""
    out = out or os.path.join(cfg.get("RDS_PULL_DIR", "data/rds"), f"{table}.parquet")
    with closing(connect(cfg, admin)) as conn:
        df = read_table(conn, table)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if out.endswith(".parquet"):
        df.to_parquet(out, index=False)
    else:
        df.to_csv(out, index=False)
    print(df)
    print(f"{table}: {df.shape[0]} rows x {df.shape[1]} columns -> {out}")
    print(df.dtypes.to_string())


def create_user(cfg, name, password):
    """Make login role `name` with read and write rights, or set a new password if the role exists.

    Only the SCRAM hash of `password` goes to the server, so the plain password is in no SQL text and no log.
    """
    role = sql.Identifier(name)
    with closing(connect(cfg, admin=True)) as conn:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [name])
            exists = cur.fetchone() is not None
            cur.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD %s" if exists else "CREATE ROLE {} LOGIN PASSWORD %s")
                        .format(role), [encrypt_password(password, name, conn, "scram-sha-256")])
            cur.execute(sql.SQL("GRANT pg_read_all_data, pg_write_all_data TO {}").format(role))
            cur.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(role))
    print(f"{name}: {'new password set' if exists else 'created'}; reads and writes every table, creates tables in public")


def ask_new_password(name):
    """Ask twice at a hidden prompt for a new password of `name`, and return it."""
    password = getpass.getpass(f"new password for {name}: ")
    if not password or password != getpass.getpass("again: "):
        raise SystemExit("empty password, or the two entries differ")
    return password


def set_password(cfg):
    """Change the password of RDS_USER: log in with the current one, then send only the SCRAM hash of the new one."""
    user = cfg.get("RDS_USER")
    if not user:
        raise SystemExit("set-password needs RDS_USER or --user; RDS owns the master password")
    with closing(connect(cfg)) as conn:
        new = ask_new_password(user)
        with conn, conn.cursor() as cur:
            cur.execute("ALTER ROLE CURRENT_USER PASSWORD %s", [encrypt_password(new, user, conn, "scram-sha-256")])
    print(f"{user}: new password set")


# ~~~~~~~~~~~~~~~~~~~~~~
# MAIN
# ~~~~~~~~~~~~~~~~~~~~~~

def main(argv=None):
    """Parse the command line and run one command. The login options go after the command name."""
    opts = argparse.ArgumentParser(add_help=False)
    opts.add_argument("--config", default="config/config.yaml", help="path to the YAML config")
    opts.add_argument("--host", help="endpoint name; overrides RDS_HOST")
    who = opts.add_mutually_exclusive_group()
    who.add_argument("--user", help="your login; overrides RDS_USER (the script asks for your password)")
    who.add_argument("--admin", action="store_true", help="the master login, with the password from AWS")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", parents=[opts], help="test the endpoint, DNS, TCP, TLS and SQL, in that order")
    p_pull = sub.add_parser("pull", parents=[opts], help="save a table to a local file")
    p_pull.add_argument("table")
    p_pull.add_argument("--out", help=".parquet or .csv path (default: RDS_PULL_DIR/<table>.parquet)")
    sub.add_parser("set-password", parents=[opts], help="change the password of --user / RDS_USER")
    sub.add_parser("create-demo", parents=[opts], help="needs --admin: replace RDS_DEMO_TABLE with synthetic rows")
    p_user = sub.add_parser("create-user", parents=[opts], help="needs --admin: make a login role, or set its new password")
    p_user.add_argument("name", help="lowercase role name, for example your own name")
    args = ap.parse_args(argv)
    if args.cmd in ("create-demo", "create-user") and not args.admin:
        raise SystemExit(f"{args.cmd} needs the master login: add --admin")
    if args.cmd == "set-password" and args.admin:
        raise SystemExit("set-password does not take --admin: it changes a personal login, and RDS owns the master password")
    cfg = load_config(args.config)
    cfg.update({k: v for k, v in {"RDS_HOST": args.host, "RDS_USER": args.user}.items() if v})
    if args.cmd == "check":
        check(cfg, args.admin)
    elif args.cmd == "pull":
        pull(cfg, args.table, args.out, args.admin)
    elif args.cmd == "set-password":
        set_password(cfg)
    elif args.cmd == "create-demo":
        create_demo(cfg)
    else:
        create_user(cfg, args.name, ask_new_password(args.name))


if __name__ == "__main__":
    main()
