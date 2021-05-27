import asyncio
import hashlib
import logging
import os
import inspect
import copy
from typing import Any, Awaitable, Callable, Union, Optional, Dict, List
import logging

AUTOCREATE = "true" in os.getenv("AUTOCREATE_TABLES", "false").lower()
MAX_RESP_LOG_LEN = int(os.getenv("MAX_RESP_LOG_LEN", 256))


LOG_LEVEL_DEBUG = bool(os.getenv("DEBUG", None))


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if LOG_LEVEL_DEBUG else logging.INFO)
    if not logger.hasHandlers():
        sh = logging.StreamHandler()
        sh.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(sh)
    return logger


try:
    import asyncpg

    DUMMY = False
except:
    from dummy_asyncpg import asyncpg

    DUMMY = True


class PGExpressions(dict):
    def __init__(self, *args, table="", **kwargs) -> None:
        self.table = table
        self.logger = get_logger(f"{self.table}_expressions")
        super().__init__(*args, **kwargs)
        if "exists" not in self:
            self[
                "exists"
            ] = f"SELECT * FROM pg_tables WHERE tablename = '{self.table}';"
        if "create_table" not in self:
            self.logger.warning(f"'create_table' not defined for {self.table}")

    def get(self, key: str, default: Any = None) -> str:
        self.logger.debug(f"self.get invoked for {key}")
        res = dict.__getitem__(self, key).replace("{self.table}", self.table)
        return res


class PGInterface:
    """Implements an abstraction for both sync and async PG requests:
    - provided a map of method names to SQL query strings
    - an optional database URI ( defaults to "")
    - and an optional event loop"""

    def __init__(self, query_strings, database="", loop=None) -> None:
        self.loop = loop or asyncio.get_event_loop()
        self.database = copy.deepcopy(database)
        self.queries = query_strings
        self.table = self.queries.table
        self.MAX_RESP_LOG_LEN = MAX_RESP_LOG_LEN
        # self.loop.create_task(self.connect_pg())
        self.connection = "PENDING:" + self.database
        if isinstance(database, dict):
            self.connection = None
            self.invocations: List[Dict] = []
        self.logger = get_logger(
            f'{self.table}{"_fake" if not self.connection else ""}_interface'
        )

    def finish_init(self):
        if not self.connection:
            self.logger.warning("RUNNING IN FAKE MODE")
        if self.connection and self.table and not self.sync_exists():
            if AUTOCREATE:
                self.sync_create_table()
                self.logger.warning(f"building table {self.table}")
            else:
                self.logger.warning(
                    f"not autocreating! table: {self.table} does not exist!"
                )
        for k in self.queries:
            if AUTOCREATE and "create" in k and "index" in k:
                self.logger.info(f"creating index via {k}")
                self.__getattribute__(f"sync_{k}")()

    async def connect_pg(self) -> None:
        self.connection = await asyncpg.connect(self.database)

    async def execute(
        self, qstring, *args, timeout=180
    ) -> Optional[Awaitable[asyncpg.Record]]:
        """Invoke the asyncpg connection's `execute` given a provided query string and set of arguments"""
        if isinstance(self.connection, str) and "PENDING" in self.connection:
            await self.connect_pg()
        if not args or args == ((),):
            args = ()
        elif args and len(args[0]):
            args = args[0]
        if self.connection:
            ret: List[asyncpg.Record] = await self.connection._execute(
                qstring, args, 0, timeout, return_status=True
            )
            return ret[0]
        return None

    def sync_execute(self, qstring: str, *args) -> asyncpg.Record:
        """Synchronous wrapper for `self.execute`"""
        ret = self.loop.run_until_complete(self.execute(qstring, *args))
        return ret

    def sync_close(self):
        self.logger.info(f"closing connection: {self.connection}")
        if self.connection:
            ret = self.loop.run_until_complete(self.connection.close())
            return ret

    def __getattribute__(self, key: str) -> Callable[..., asyncpg.Record]:
        """Implicitly define methods on this class for every statement in self.query_strings.
        If method is prefaced with "sync_": wrap as a synchronous function call.
        If statement in self.query_strings looks like an f-string, treat it as such by evaling
        before passing to `executer`."""
        try:
            return object.__getattribute__(self, key)
        except AttributeError:
            pass
        if key.startswith(
            "sync_"
        ):  # sync_ prefix implicitly wraps query as synchronous
            qstring = key.replace("sync_", "")
            executer = self.sync_execute
        else:
            executer = self.execute
            qstring = key
        statement = self.queries.get(qstring)
        if not statement:
            raise ValueError(f"No statement of name {qstring} or {key} found!")

        def truncate(thing: str) -> str:
            if len(thing) > self.MAX_RESP_LOG_LEN:
                return f"{thing[:self.MAX_RESP_LOG_LEN]}...[{len(thing)-self.MAX_RESP_LOG_LEN} omitted]"
            else:
                return thing

        if not self.connection and self.database is not None:
            canned_response = self.database.get(qstring, [[None]]).pop(0)
            if qstring in self.database and not len(self.database.get(qstring, [])):
                self.database.pop(qstring)

            def return_canned(*args, **kwargs):
                self.invocations.append({qstring: (args, kwargs)})
                if callable(canned_response):
                    resp = canned_response(*args, **kwargs)
                else:
                    resp = canned_response
                short_strresp = truncate(f"{resp}")
                self.logger.info(
                    f"returning `{short_strresp}` for expression: "
                    f"`{qstring}` eval'd with `{args}` & `{kwargs}`"
                )
                return resp

            return return_canned
        elif "$1" in statement or "{" in statement and "}" in statement:

            def executer_with_args(*args):
                """Closure over 'statement' in local state for application to arguments.
                Allows deferred execution of f-strs, allowing PGExpresssions to operate on `args`."""
                rebuilt_statement = eval(f"f'{statement}'")
                if (
                    rebuilt_statement != statement
                    and "args" in statement
                    and "$1" not in statement
                ):
                    args = ()
                resp = executer(rebuilt_statement, args)
                short_strresp = truncate(f"{resp}")
                self.logger.debug(f"{rebuilt_statement} {args} -> {short_strresp}")
                return resp

            return executer_with_args
        else:

            def executer_without_args():
                """Closure over local state for executer without arguments."""
                return executer(statement)

            return executer_without_args
