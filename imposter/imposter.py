#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import json
from re import template
from forest import utils
from forest.core import Bot, Message, Response, run_bot

# from acrossword import Document, DocumentCollection, Ranker

# The bot class is analogous to the Agent class in Personate.
# It should set up a Frame, Ranker, Filter, knowledge and examples
# It should set Activators, (as synonyms of a do_activate function?)
#  - this seems to be trickier than i thought with synonyms
# Can we set a Face here as well? (avatar, profile name etc)
# Can we build a Memory here? from pdictng?


def template_example(name: str, example: dict):
    source = ""
    if "source" in example:
        source = f"(Source: {example['source']})\n\n"
    final_example = f"user: {example['user']}\n\n{source}{name}: {example['agent']}"
    return final_example


class Imposter(Bot):
    def __init__(self) -> None:
        # Accept a JSON config file in the same format as Personate.
        # Can be generated at https://ckoshka.github.io/personate/
        config_file = utils.get_secret("CONFIG_FILE")
        with open(config_file, "r") as f:
            config = json.load(f)
        self.preset = config["preset"]
        self.name = config["name"]
        self.description = config["introduction"]
        self.avatar_url = config["avatar"]
        self.activators = [o["listens_to"] for o in config["activators"]]
        self.reading_list = config["reading_list"]
        self.examples = [
            template_example(self.name, example) for example in config["examples"]
        ]
        super().__init__()

    def match_command(self, msg: Message) -> str:
        if not msg.arg0:
            return ""
        # Look for direct match before checking activators
        if hasattr(self, "do_" + msg.arg0):
            return msg.arg0
        # Try activators
        if msg.arg0 in self.activators:
            return "activate"
        # Pass the buck
        return super().match_command(msg)

    async def do_activate(self, _: Message) -> str:
        return self.description


if __name__ == "__main__":
    run_bot(Imposter)
