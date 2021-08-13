import asyncio
import forest_tables, utils

utils.load_secrets("dev")
dev = forest_tables.RoutingManager(database=utils.get_secret("DATABASE_URL"))
utils.load_secrets("staging")
staging = forest_tables.RoutingManager(database=utils.get_secret("DATABASE_URL"))
utils.load_secrets("prod")
prod = forest_tables.RoutingManager(database=utils.get_secret("DATABASE_URL"))


async def dedup():
    dup_stage = [
        record.get("id")
        for record in (await staging.execute("select id from routing"))
        if record in (await prod.execute("select id from routing"))
    ]
    dup_dev = [
        record.get("id")
        for record in (await dev.execute("select id from routing"))
        if record in (await staging.execute("select id from routing"))
    ]
    for number in dup_stage:
        print(f"deleting duplicate record {number} from staging")
        await staging.delete(number)
    for number in dup_dev:
        print(f"deleting duplicate record {number} from staging")
        await staging.delete(number)


if __name__ == "__main__":
    asyncio.run(dedup())
