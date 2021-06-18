import os
from pghelp import PGExpressions, PGInterface, Loop


if os.path.exists("dev_secrets") and not os.getenv("DATABASE_URL"):
    print("environ'ing secrets")
    secrets = dict(line.strip().split("=", 1) for line in open("dev_secrets"))
    os.environ.update(secrets)

USER_DATABASE = os.environ["DATABASE_URL"]


ROUTING_DATABASE = os.environ["DATABASE_URL"]

RoutingPGExpressions = PGExpressions(
    table="routing",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} (id TEXT PRIMARY KEY, destination CHARACTER VARYING(16), expiration_ms BIGINT);",
    get_destination="SELECT destination FROM {self.table} WHERE id=$1 AND (expiration_ms > extract(epoch from now()) * 1000 OR expiration_ms is NULL);",
    get_id="SELECT id FROM {self.table} WHERE destination=$1;",
    set_destination="UPDATE {self.table} SET destination=$2 WHERE id=$1;",
    set_expiration_ms="UPDATE {self.table} SET expiration_ms=$2 WHERE id=$1;",
    put_destination="INSERT INTO {self.table} (id, destination) VALUES($1, $2) ON CONFLICT DO NOTHING;",
    sweep_expired_destinations="DELETE FROM {self.table} WHERE expiration_ms IS NOT NULL AND expiration_ms < (extract(epoch from now()) * 1000);",
)

GroupRoutingPGExpressions = PGExpressions(
    table="routing",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} \
        (id TEXT PRIMARY KEY, external_phone_number CHARACTER VARYING(16), \
        signal_destination CHARACTER VARYING(16), \
        group_id CHARACTER VARYING(16));",
    get_group_id="SELECT group_id FROM {self.table} WHERE external_phone_number=$1;",
    get_external_number="SELECT external_phone_number FROM {self.table} WHERE group_id=$1",
    put_new_group="INSERT INTO {self.table} (external_phone_number, group_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
)

PaymentsPGExpressions = PGExpressions(
    table="payments",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} (transaction_log_id CHARACTER VARYING(16) PRIMARY KEY, \
            account_id CHARACTER VARYING(16), \
            value_pmob BIGINT, \
            finalized_block_index BIGINT, \
            timestamp_ms BIGINT, \
            expiration_ms BIGINT);",
    get_payment="SELECT * FROM {self.table} WHERE value_pmob=$1 AND expiration_ms < (extract(epoch from now())+3600) * 1000;",
    put_payment="INSERT INTO {self.table} (transaction_log_id, account_id, value_pmob, finalized_block_index, timestamp_ms, expiration_ms) \
                                    VALUES($1, $2, $3, $4, extract(epoch from now()) * 1000, (extract(epoch from now())+3600) * 1000) ON CONFLICT DO NOTHING",
)


class RoutingManager(PGInterface):
    def __init__(
        self,
        queries: PGExpressions = RoutingPGExpressions,
        database: str = USER_DATABASE,
        loop: Loop = None,
    ) -> None:
        super().__init__(queries, database, loop)


class GroupRoutingManager(PGInterface):
    def __init__(
        self,
        queries: PGExpressions = GroupRoutingPGExpressions,
        database: str = USER_DATABASE,
        loop: Loop = None,
    ) -> None:
        super().__init__(queries, database, loop)


class PaymentsManager(PGInterface):
    """Abstraction for operations on the `user` table."""

    def __init__(
        self,
        queries: PGExpressions = PaymentsPGExpressions,
        database: str = USER_DATABASE,
        loop: Loop = None,
    ) -> None:
        super().__init__(queries, database, loop)
