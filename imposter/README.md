Imposter is a bot that uses prompt engineering and large language models to mimic a character. It takes inspiration (and maybe code) from [Personate, the zero-shot chatbot library for large language models](https://github.com/ckoshka/personate), but translates the underlying concepts to the context of bots on Signal instead of Discord.

## What is Personate? 
Personate is a library that wraps the Jurassic-1 large language model with prompt engineering and semantic search capabilities to imPersonate historical persons, advanced AIs, fictional characters or other types of guy.

Personate has several built-in features that give it an advantage over raw language model outputs, including:
- access to predefined datasets and conversation examples
- pseudocode interpretation
- filtering out inappropriate speech, adversarial inputs, and repetitive answers

Despite this, it' s still based on a generative model, so outputs are not deterministic. Initial tests suggest that it might be useful for a FAQ bot, but it responds with hallucinatory information too often to be a reliable face for MobileCoin. It also sends messages to the Jurassic-1 API at AI21, so there are some privacy concerns there.

Currently the best use-case for Imposter is probably an artbot that gets people engaged with the MobileCoin/Forest ecosystem, similar to Imogen. A historical or mythical character could be chosen for their connotations or just how fun they are to "talk" to. Further engagement with MobileCoin could come in the form of priority queue, DM permissions, or subscription to regular outputs like a newsletter.

Stretch goal for Imposter: have one bot that can spin up other bots from examples/knowledge/personality description given directly in chat. This requires some infrastructure we don't currently have, but could be a sustainable business model along the lines of Replika.ai.

Imposter could also work together with Imogen, supplementing the AI artist by coming up with creative prompts. A team duo. 

## How can we adapt it to Signal?
Personate is designed around the constraints of Discord. This means it has a different object model than Forest bots do, and can make use of different affordances. 

Things Personate does that are not possible (so far as I know) in Signal:
- Simulate multiple bots from one account with webhooks (only one Face per bot phone #?)
- Embed a loading image or message, then edit that message to replace with generated text (can simulate with TypingMessage)

Things for which Personate uses Discord-specific APIs, which will require work to convert for Signal:
- Follow reply chains 
- Access channel message history
- Modify bot live through direct commands
- Store conversation history, etc, in memory
- Run an improvised adventure scene by scene

Things Personate does which are not Discord-specific, and could be imported or mimicked pretty directly:
- Wrap each conversational turn in a well-engineered prompt
- Maintain a personality and conversational context from turn to turn
- Access knowledgebases to get appropriate information into the prompt
- Access pre-written conversation examples and include appropriate examples in the prompt
- Filter the resulting prompts for hate speech and other problematic outputs, retrying until it gets something usable
- Access Python functions and API calls to get an appropriate function, then create a prompt with the function, docstring and args and get a result from the language model

## Who should the bot impersonate?
Suggestions gathered from Forest team and Twitter at large (some were suggested multiple times):
```
Francis Bacon 	    	2	
Adam Sandler
Marshall McLuhan
Terence McKenna
Baron Munchausen
Andrea Dworkin
Andrew Jackson
W.A. Mozart
Virgin Mary
Karl Marx
Giordano Bruno
Emmy Noether
Pascal
Poisson
Michel Foucault
Rosa Luxembourg
Hildegard von Bingen
Ada Lovelace
Simone de Bouveoir
Virginia Woolf
Alexandra David Neel
Dian Fossey
Sherlock Holmes
Ayn Rand
HP Lovecraft
Jesus
Leo Tolstoy
Orson Welles
Adolf Hitler    		2
Rasputin
Carl Jung		    	2
Isaac Asimov
Nietzsche
Richard Feynman
Aretino
Walt Disney
Shakespeare
Rodney Dangerfield
Yogi Berra
St Jude
Douglas Adams
Mark Twain
Oscar Wilde 	    	3
Socrates
Ludwig Wittgenstein
Zhuge Liang
Rumi
Martin Luther King Jr
Christopher Hitchens
Buckminster Fuller
Samuel Pepys
Allen Dulles
Odin
Leopold Bloom
```