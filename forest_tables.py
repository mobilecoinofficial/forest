import os
from pghelp import PGExpressions, PGInterface, Loop


if os.path.exists("secrets") and not os.getenv("USER_DATABASE"):
    print("environ'ing secrets")
    os.environ.update(
        {
            key: value
            for key, value in [
                line.strip().split("=", 1)
                for line in open("secrets").read().split()
            ]
        }
    )
USER_DATABASE = os.environ["USER_DATABASE"]

UserPGExpressions = PGExpressions(
    table="prod_users",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} \
            (id TEXT PRIMARY KEY, \
            account JSON, \
            last_update_ms BIGINT, \
            last_claim_ms BIGINT, \
            active_node_name TEXT);",
    get_user="SELECT account FROM {self.table} WHERE id=$1;",  # AND
    mark_user_claimed="UPDATE {self.table} \
        SET active_node_name = $2, \
        last_claim_ms = (extract(epoch from now()) * 1000) \
        WHERE id=$1;",
    mark_user_freed="UPDATE {self.table} SET last_claim_ms = 0, active_node_name = NULL WHERE id=$1;",
    get_free_user="SELECT (id, account) FROM {self.table} \
            WHERE active_node_name IS NULL \
            AND last_claim_ms = 0 \
            LIMIT 1;",
    mark_user_update="UPDATE {self.table} SET \
        last_update_ms = (extract(epoch from now()) * 1000) \
        WHERE id=$1;",
    set_user="UPDATE {self.table} SET \
            account = $2, \
            last_update_ms = (extract(epoch from now()) * 1000) \
            WHERE id=$1;",
    put_user="INSERT INTO {self.table} (id, account) \
            VALUES($1, $2) ON CONFLICT DO NOTHING;",
    sweep_leaked_users="UPDATE {self.table} \
            SET last_claim_ms = 0, active_node_name = NULL \
            WHERE last_update_ms < ((extract(epoch from now())-3600) * 1000);",
)

ROUTING_DATABASE = os.getenv("USER_DATABASE")

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


class UserManager(PGInterface):
    """Abstraction for operations on the `user` table."""

    def __init__(
        self,
        queries: PGExpressions = UserPGExpressions,
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
