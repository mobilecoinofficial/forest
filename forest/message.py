from typing import Optional

class JsonRpcMessage:
    """
    Represents json rpc message received from signal-cli in jsonRpc mode, optionally containing a command with arguments.
    """

    def __init__(self, blob: dict) -> None:
        self.blob = blob
        
        # json rpc core data
        if blob.get("jsonrpc") != "2.0":
            raise ValueError("jsonrpc must be version 2.0")
        
        self.jsonrpc = blob.get("jsonrpc")
        self.id = blob.get("id")
        self.method = blob.get("method")
        self.params = params = blob.get("params",{})
        self.error = error = blob.get("error",{})
        self.result = result = blob.get("result",{})
    
        # fields if receive command
        if self.method == "receive":
            #get standard envelope info
            self.envelope = envelope = params.get("envelope", {})
            self.source_uuid = envelope.get("sourceUuid")
            self.source_number = envelope.get("sourceNumber")
            self.timestamp = envelope.get("timestamp")

            #if there's a dataMessage, parse it
            self.msgobj = msg = params.get("dataMessage", {})
            self.group_info = msg.get("groupInfo", {})
            self.attachments = msg.get("attachments", {})
            self.message = msg.get("message")
            self.metions = msg.get("mentions",())

            #get other possible signal-cli rpc taxonomies
            self.receipt_message = params.get("receiptMessage")
            self.typing_message = params.get("typingMessage")
            self.receive_error = params.get("error")

        if self.error:
            self.error_code = error.get("code")
            self.error_message = error.get("message")

        if self.result:
            self.timestamp = result.get("timestamp")

    #Discovery on what jsonrpc2.0 spec primitive it is
    def is_request(self) -> bool:
        """Determines if jsonrpc blob is a request"""
        return ("method" in self.blob)

    def is_error(self) -> bool:
        """Determines if jsonrpc blob is an error"""
        return ("error" in self.blob)

    def is_response(self) -> bool:
        """Determine if jsonrpc blob is a response"""
        return ("result" in self.blob)

    def __getattr__(self, attr) -> str:
        # return falsy string back if not found
        return ""

class StdioMessage:
    """Represents a Message received from signal-cli, optionally containing a command with arguments."""

    def __init__(self, blob: dict) -> None:
        self.blob = blob
        self.envelope = envelope = blob.get("envelope", {})
        # {'envelope': {'source': '+15133278483', 'sourceDevice': 2, 'timestamp': 1621402445257, 'receiptMessage': {'when': 1621402445257, 'isDelivery': True, 'isRead': False, 'timestamps': [1621402444517]}}}

        # envelope data
        self.source: str = envelope.get("source")
        self.name: str = envelope.get("sourceName") or self.source
        self.timestamp = envelope.get("timestamp")

        # msg data
        msg = envelope.get("dataMessage", {})
        self.full_text = self.text = msg.get("message", "")
        self.group: Optional[str] = msg.get("groupInfo", {}).get("groupId")
        self.quoted_text = msg.get("quote", {}).get("text")
        self.payment = msg.get("payment")

        # parsing
        self.command: Optional[str] = None
        self.tokens: Optional[list[str]] = None
        if self.text and self.text.startswith("/"):
            command, *self.tokens = self.text.split(" ")
            self.command = command[1:]  # remove /
            self.arg1 = self.tokens[0] if self.tokens else None
            self.text = " ".join(self.tokens)
        # self.reactions: dict[str, str] = {}

    def __repr__(self) -> str:
        # it might be nice to prune this so the logs are easier to read
        return f"<{self.envelope}>"



