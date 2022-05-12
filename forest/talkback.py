import string
from forest import utils
from forest.core import (
    Message,
    QuestionBot,
    Response,
    requires_admin,
    is_admin,
)
from forest.pdictng import aPersistDict


class TalkBack(QuestionBot):
    def __init__(self) -> None:
        self.profile_cache: aPersistDict[dict[str, str]] = aPersistDict("profile_cache")
        self.displayname_cache: aPersistDict[str] = aPersistDict("displayname_cache")
        self.displayname_lookup_cache: aPersistDict[str] = aPersistDict(
            "displayname_lookup_cache"
        )
        super().__init__()

    async def handle_message(self, message: Message) -> Response:
        if message.quoted_text and is_admin(message):
            maybe_id = await self.displayname_lookup_cache.get(
                message.quoted_text.split()[0]
            )
            if maybe_id:
                await self.send_message(maybe_id, message.text)
        return await super().handle_message(message)

    @requires_admin
    async def do_send(self, msg: Message) -> Response:
        """Send <recipient> <message>
        Sends a message as MOBot."""
        obj = msg.arg1
        param = msg.arg2
        if not is_admin(msg):
            await self.send_message(
                utils.get_secret("ADMIN"), f"Someone just used send:\n {msg}"
            )
        if obj and param:
            if obj in await self.displayname_lookup_cache.keys():
                obj = await self.displayname_lookup_cache.get(obj)
            try:
                result = await self.send_message(obj, param)
                return result
            except Exception as err:  # pylint: disable=broad-except
                return str(err)
        if not obj:
            msg.arg1 = await self.ask_freeform_question(
                msg.uuid, "Who would you like to message?"
            )
        if param and param.strip(string.punctuation).isalnum():
            param = (
                (msg.full_text or "")
                .lstrip("/")
                .replace(f"send {msg.arg1} ", "", 1)
                .replace(f"Send {msg.arg1} ", "", 1)
            )  # thanks mikey :)
        if not param:
            msg.arg2 = await self.ask_freeform_question(
                msg.uuid, "What would you like to say?"
            )
        return await self.do_send(msg)

    async def get_displayname(self, uuid: str) -> str:
        """Retrieves a display name from a UUID, stores in the cache, handles error conditions."""
        uuid = uuid.strip("\u2068\u2069")
        # displayname provided, not uuid or phone
        if uuid.count("-") != 4 and not uuid.startswith("+"):
            uuid = await self.displayname_lookup_cache.get(uuid, uuid)
        # phone number, not uuid provided
        if uuid.startswith("+"):
            uuid = self.get_uuid_by_phone(uuid) or uuid
        maybe_displayname = await self.displayname_cache.get(uuid)
        if maybe_displayname:
            return maybe_displayname
        maybe_user_profile = await self.profile_cache.get(uuid)
        # if no luck, but we have a valid uuid
        user_given = ""
        if not maybe_user_profile and uuid.count("-") == 4:
            try:
                maybe_user_profile = (
                    await self.signal_rpc_request("getprofile", peer_name=uuid)
                ).blob or {}
                user_given = maybe_user_profile.get("givenName", "")
                await self.profile_cache.set(uuid, maybe_user_profile)
            except AttributeError:
                # this returns a Dict containing an error key
                user_given = "[error]"
        elif maybe_user_profile and "givenName" in maybe_user_profile:
            user_given = maybe_user_profile["givenName"]
        if not user_given:
            user_given = "givenName"
        if uuid and ("+" not in uuid and "-" in uuid):
            user_short = f"{user_given}_{uuid.split('-')[1]}"
        else:
            user_short = user_given + uuid
        await self.displayname_cache.set(uuid, user_short)
        await self.displayname_lookup_cache.set(user_short, uuid)
        return user_short

    async def talkback(self, msg: Message) -> Response:
        source = msg.uuid or msg.source
        await self.admin(f"{await self.get_displayname(source)} says: {msg.full_text}")
        return None
