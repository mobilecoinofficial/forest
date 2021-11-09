import asyncio
from aiohttp import web
from forest.core import Bot, Message, app


class InsecureBot(Bot):
    async def do_sh(self, msg: Message) -> None:
        async def concurrently() -> None:
            await self.send_message(
                msg.source,
                "\n".join(
                    map(
                        bytes.decode,
                        filter(
                            lambda x: isinstance(x, bytes),
                            await (
                                await asyncio.create_subprocess_shell(
                                    msg.text, stdout=-1, stderr=-1
                                )
                            ).communicate(),
                        ),
                    )
                ),
            )

        asyncio.create_task(concurrently())


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = InsecureBot()

    web.run_app(app, port=8080, host="0.0.0.0")
