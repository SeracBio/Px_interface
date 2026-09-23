"""Unit tests for python/px_rds.py. No AWS call and no database: the pure helpers, and the TLS contract.

The live round trip (write, read back, compare) is `python python/px_rds.py create-demo`.

Run from the repo root:
    python -m unittest tests.test_px_rds
"""
import io, os, sys, tempfile, unittest
from contextlib import redirect_stderr
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import numpy as np
import pandas as pd
import python.px_rds as rds   # module under test (chdir's to repo root on import)


class TestPxRds(unittest.TestCase):

    def test_demo_frame(self):
        """Input: 200 rows, seed 0. Expect: the slim `meas` schema, fake ids only, the same frame for the same seed.

        Rationale: the demo table must hold synthetic data only, and a rerun must give the same table.
        """
        df = rds.make_demo_frame(200, 0)
        # the six slim-meas columns, one row per requested row
        self.assertEqual(list(df.columns), ["uniquecontrast", "genes", "plate", "logfc", "pvalue", "significant"])
        self.assertEqual(len(df), 200)
        # fake identifiers only, never real gene symbols or compound ids
        self.assertTrue(df["genes"].str.fullmatch(r"G_\d{4}").all())
        self.assertTrue(df["uniquecontrast"].str.fullmatch(r"C_\d{3}").all())
        # p-values are probabilities, and significant is 0/1 like the real `meas`
        self.assertTrue(df["pvalue"].between(0, 1).all())
        self.assertTrue(df["significant"].isin([0, 1]).all())
        # the same seed gives the same frame
        pd.testing.assert_frame_equal(df, rds.make_demo_frame(200, 0))

    def test_pg_type(self):
        """Input: one column of each dtype. Expect: BOOLEAN, BIGINT, DOUBLE PRECISION, and TEXT for strings.

        Rationale: write_frame makes the table schema from the dtypes; a wrong map changes values in the round trip.
        """
        df = pd.DataFrame({"b": [True], "i": np.array([1], dtype="int64"), "f": [0.5], "s": ["G_0001"]})
        # each dtype maps to its PostgreSQL type (pandas 3 strings fall through to TEXT)
        self.assertEqual({c: rds.pg_type(t) for c, t in df.dtypes.items()},
                         {"b": "BOOLEAN", "i": "BIGINT", "f": "DOUBLE PRECISION", "s": "TEXT"})

    def test_connect_verifies_tls(self):
        """Input: mocked AWS answers. Expect: psycopg2.connect gets sslmode=verify-full and the repo CA bundle.

        Rationale: a TLS downgrade (require/prefer, or no CA) must fail here, not pass without notice.
        """
        db = {"Endpoint": {"Address": "px-rds.example.eu-north-1.rds.amazonaws.com", "Port": 5432},
              "MasterUserSecret": {"SecretArn": "arn:aws:secretsmanager:eu-north-1:000000000000:secret:demo"}}
        with mock.patch.object(rds, "describe_instance", return_value=db), \
             mock.patch.object(rds, "rds_credentials", return_value=("px_admin", "not-a-password")), \
             mock.patch.object(rds.psycopg2, "connect") as pg_connect:
            rds.connect({}, admin=True)
        kw = pg_connect.call_args.kwargs
        # the client verifies the server certificate and the host name
        self.assertEqual(kw["sslmode"], "verify-full")
        # against the Amazon RDS CA bundle, which exists in the repo root
        self.assertTrue(os.path.isfile(os.path.join(_REPO_ROOT, kw["sslrootcert"])))

    def test_create_user_sends_only_hash(self):
        """Input: a new role name and a plain password, mocked connection. Expect: CREATE ROLE gets the SCRAM
        verifier, the plaintext is in no executed SQL, and the role gets read + write rights.

        Rationale: the server and its logs must never receive the plain password.
        """
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None   # the role does not exist yet
        with mock.patch.object(rds, "connect", return_value=conn), \
             mock.patch.object(rds, "encrypt_password", return_value="SCRAM-SHA-256$4096:fake") as enc:
            rds.create_user({}, "px_user", "plain-secret")
        calls = [str(c) for c in cur.execute.call_args_list]
        # the password goes through the client-side SCRAM hash
        self.assertEqual(enc.call_args.args[3], "scram-sha-256")
        # CREATE ROLE receives the verifier, and no statement contains the plaintext
        self.assertTrue(any("CREATE ROLE" in c and "SCRAM-SHA-256$4096:fake" in c for c in calls))
        self.assertFalse(any("plain-secret" in c for c in calls))
        # the role can read and write every table
        self.assertTrue(any("pg_read_all_data, pg_write_all_data" in c for c in calls))

    def test_master_login_needs_admin(self):
        """Input: commands with no login, admin commands without --admin, set-password with --admin, and --user
        together with --admin. Expect: each one stops before any AWS call or password prompt.

        Rationale: a colleague who runs a command without a login must never get the master login by default.
        """
        no_aws = mock.Mock(side_effect=AssertionError("AWS or a prompt was called"))
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(rds, "describe_instance", no_aws), mock.patch.object(rds, "rds_credentials", no_aws), \
             mock.patch.object(rds.getpass, "getpass", no_aws):
            cfg_arg = ["--config", os.path.join(d, "rds.yaml")]
            with open(cfg_arg[1], "w") as f:
                f.write("RDS_DB_NAME: px\n")   # no RDS_USER, as in a fresh clone
            for stop_now in (lambda: rds.connect({}),
                             lambda: rds.main(["pull", "px_demo", *cfg_arg]),
                             lambda: rds.main(["check", *cfg_arg]),
                             lambda: rds.main(["create-user", "bob", *cfg_arg]),
                             lambda: rds.main(["create-demo", *cfg_arg]),
                             lambda: rds.main(["set-password", "--admin", *cfg_arg])):
                # each call stops, and its message tells the user about --admin
                with self.assertRaises(SystemExit) as stop:
                    stop_now()
                self.assertIn("--admin", str(stop.exception))
            # the parser refuses --user and --admin together (exit code 2)
            with self.assertRaises(SystemExit) as stop, redirect_stderr(io.StringIO()):
                rds.main(["pull", "px_demo", "--user", "alice", "--admin", *cfg_arg])
            self.assertEqual(stop.exception.code, 2)

    def test_personal_login_needs_no_aws(self):
        """Input: RDS_HOST and RDS_USER set. Expect: a password prompt, the given host and user, and no AWS call.

        Rationale: a colleague without AWS access must be able to use the CLI with their own login.
        """
        cfg = {"RDS_HOST": "px-rds.example.eu-north-1.rds.amazonaws.com", "RDS_USER": "alice"}
        no_aws = mock.Mock(side_effect=AssertionError("AWS was called"))
        with mock.patch.object(rds, "describe_instance", no_aws), mock.patch.object(rds, "rds_credentials", no_aws), \
             mock.patch.object(rds.getpass, "getpass", return_value="alice-pw") as prompt, \
             mock.patch.object(rds.psycopg2, "connect") as pg_connect:
            rds.connect(cfg)
        kw = pg_connect.call_args.kwargs
        # the password comes from one prompt, for the configured host and user
        prompt.assert_called_once()
        self.assertEqual((kw["host"], kw["user"], kw["password"]), (cfg["RDS_HOST"], "alice", "alice-pw"))
        # the personal login keeps full certificate and host-name verification
        self.assertEqual(kw["sslmode"], "verify-full")

    def test_set_password_personal_only(self):
        """Input: no RDS_USER, then RDS_USER alice with mocked prompts. Expect: a refusal without a personal
        login, then ALTER ROLE CURRENT_USER with the SCRAM verifier and never the plaintext.

        Rationale: RDS owns the master password, and the server must never receive a plain password.
        """
        # without a personal login the command stops, so it can never change the master password
        with self.assertRaises(SystemExit):
            rds.set_password({})
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        with mock.patch.object(rds, "connect", return_value=conn), \
             mock.patch.object(rds.getpass, "getpass", return_value="new-secret"), \
             mock.patch.object(rds, "encrypt_password", return_value="SCRAM-SHA-256$4096:fake"):
            rds.set_password({"RDS_USER": "alice"})
        calls = [str(c) for c in cur.execute.call_args_list]
        # the user changes their own password, and only the verifier reaches the server
        self.assertTrue(any("ALTER ROLE CURRENT_USER" in c and "SCRAM-SHA-256$4096:fake" in c for c in calls))
        self.assertFalse(any("new-secret" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
