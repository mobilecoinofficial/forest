from io import BytesIO
from tarfile import TarFile
from typing import Union
import asyncio
import base64
import json
import os
import shutil
import asyncpg
import datastore

prod_db: str = dict(line.strip().split("=", 1) for line in open("prod_secrets"))[
    "USER_DATABASE"
]
dev_secrets: dict[str, str] = dict(
    line.strip().split("=", 1) for line in open("dev_secrets")
)
dev_db: str = dev_secrets["DATABASE_URL"]


# dev_accounts = PGInterface(
#     query_strings=datastore.AccountPGExpressions,
#     database=dev_db,
# )
# prod_accounts = PGInterface(
#     query_strings=datastore.AccountPGExpressions,
#     database=prod_db,
# )


def load_any(data: Union[bytes, str]) -> None:
    try:
        loaded_data = json.loads(data)
        if "username" in loaded_data:
            print("json")
            try:
                os.mkdir("data")
            except FileExistsError:
                pass
            open("data/" + loaded_data["username"], "w").write(data)
            return
        tardata = base64.urlsafe_b64decode(loaded_data["tarball"].encode())
    except json.JSONDecodeError:
        tardata = data

    tarball = TarFile(fileobj=BytesIO(tardata))
    print(tarball.getmembers())
    tarball.extractall()
    return


async def main() -> None:
    try:
        os.mkdir("/tmp/migrate-forest-db")
    except FileExistsError:
        pass
    os.chdir("/tmp/migrate-forest-db")
    print(os.getcwd())
    prod = await asyncpg.connect(prod_db)
    dev = await asyncpg.connect(dev_db)
    for row in await prod.fetch("select * from prod_users"):
        number = row.get("id")

        if not ("617" in number or number == dev_secrets["BOT_NUMBER"]):
            continue
        print(number)
        data = row.get("account") or row.get("datastore")
        print(data[:10])
        load_any(data)
        for dirpath, dirs, files in os.walk("."):
            for fname in dirs + files:  # iterate over items form both lists
                print(os.path.join(dirpath, fname))
        create = not await dev.fetch("select * from signal_accounts where id=$1", number)
        await (await datastore.SignalDatastore(number)).upload(create=create)
        shutil.rmtree("data")


asyncio.run(main())
