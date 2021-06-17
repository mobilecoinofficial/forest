class Message:
    group: Optional[str]
    quoted_text: Optional[str]

    def __getattribute__(self, key) -> Optional[Union[str, list, dict]]:
        return self.envelope.get(key)

class Command(Message):
    pass

class BotAccount:
    def __init__(self, number: str):
        self.number = number

    async def load(self):
        result = accounts_table.connection.execute("select * where id=$1", self.number)
        # unzip tarball etc
        if not result:
            Teli.buy()
        if not result.account["registered"] :
            self.register()

    async def save(self):
        pass

    async def handle_memfs_weirdness(self):
        pass

class Signal:
    account: BotAccount
    output_queue: Queue

    def handle_nonmessages(blob: dict):
        if "group" in blob:
            pass
        if "error" in blob:
            pass

    def signal_line(command: dict) -> None:
        pass

    def send(recipient: str, message: str) -> None:
        # we could include a handler to catch signal responding
        # with a timestamp but that's Hard To Time Correctly

class Teli:
    # start inbound handler and queue
    # figure out our url, punch a localtunnel if we're testing locally
    # set sms post url 
    def search(**kwargs) -> list[str]:
        pass

    def buy(number: str) -> dict:
        pass

    def send(source, destination, message) -> dict:
        pass

class CommandHandler:
    def do_register(cmd: Command) -> str:
        pass

    def do_mkgroup(cmd: Command) -> str:
        pass
    do_query = do_mkgroup

class Orchestator:
    def start(bot_account_number: str):
        self.signal = Signal()
        self.teli = Teli()
        self.command_handler = CommandHandler(self)

