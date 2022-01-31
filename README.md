# A Forest of Signal Bots #

Forest is a framework for running payments-enabled chat and utility bots for [Signal Messenger](https://signal.org/en/).

To get familiarised with deploying and running a forest bot, we've provided a short tutorial to teach you how to deploy hellobot, the simplest possible forest bot.

## High Level Overview ##

In this tutorial you will:

 * Install Pre-Requisites.
 * Install signal-cli and register a Signal account for your bot.
 * Deploy the bot!

At the end there's extra information and a guide on how to contribute.

## Bring your own phone number ##

You will need at least 2 Signal accounts to properly test your bot. A signal account for the bot to run on and your own signal account to talk to the bot. To set up an additional signal account for your bot you can use a second phone or a VoIP service such as Google Voice, [Forest Contact](/contact), Twilio, or Teli.net. All you need is a phone number that can receive SMS.


## Installing Prerequisites ##

### Python 3.9 ###

Please refer to the [official Python wiki](https://wiki.python.org/moin/BeginnersGuide/Download)
for instructions on installing Python 3.9 on your machine. On Debian/Ubuntu based systems you may run:

```bash
sudo apt update
sudo apt install python3.9 python3.9-dev python3-pip
```

### Dependencies ###

We use pipenv to handle dependencies, run:

```bash
python3.9 -m pip install pipenv
```
then to install the prerequisites:
```bash
pipenv install
```

</br>

## Signal-cli ##

[Signal-cli](https://github.com/AsamK/signal-cli) is a command line interface for Signal. Forest bots run with signal-cli or [auxin-cli](https://github.com/mobilecoinofficial/auxin-cli) as the backend. Auxin-cli is beta software, and does not yet allow to register a new phone number, so for this guide we will use signal-cli.

To install or run signal-cli you will need Java 17. Verify that you have it installed by running:
```bash
java --version
---
openjdk 17.0.1 2021-10-19
OpenJDK Runtime Environment (build 17.0.1+12-Ubuntu-120.04)
OpenJDK 64-Bit Server VM (build 17.0.1+12-Ubuntu-120.04, mixed mode, sharing)
```

otherwise install with:
```bash
sudo apt install openjdk-17-jre-headless
```

You can then install signal-cli from a pre-built release or build it from source yourself.

<br>

### Download a pre-built signal-cli release ###

The maintainers of signal-cli provide precompiled releases you can download and run immediately.

Download and extract the latest release tarball from https://github.com/AsamK/signal-cli/releases
```bash
wget https://github.com/AsamK/signal-cli/releases/download/v0.10.0/signal-cli-0.10.0.tar.gz

tar -xvf signal-cli-0.10.0.tar.gz
```
Verify the installation succeeded

``` bash
./signal-cli-0.10.0/bin/signal-cli --version
---
signal-cli 0.10.0
```

Finally for ease of use, link the executable to your working directory:

``` bash
ln -s ./signal-cli-0.10.0/bin/signal-cli .

./signal-cli --version
---
signal-cli 0.10.0
```

### Building signal-cli from Source ###

You can also build signal-cli from source. You can do so by cloning the official repo and running `./gradlew installDist`:

``` bash
git clone https://github.com/AsamK/signal-cli.git

cd signal-cli

./gradlew installDist
```
Verify the installation succeeded:

``` bash
./build/install/signal-cli/bin/signal-cli --version
---
signal-cli 0.10.0
```

Finally for ease of use, link the executable to your working directory (change the path depending on where you cloned the repo):

``` bash

ln -s $HOME/signal-cli/build/install/signal-cli/bin/signal-cli .

./signal-cli --version
---
signal-cli 0.10.0
```

For more detailed instructions visit the [signal-cli repository](https://github.com/AsamK/signal-cli).

<br>

## Registering a Signal Account for your bot

As mentioned above, you will need at least 2 Signal accounts to properly test your bot. A Signal account for the bot to run on and your own signal account to talk to the bot. To set up an additional Signal account for your bot you can use a second phone or a VoIP service such as Google Voice, [Forest Contact](/contact), or Twilio. All you need is a phone number that can receive SMS.

We've deviced a shortcut to register a Signal data store. Input your phone number with the country code (+1 for the US) and then run these commands to obtain a Signal datastore in a data folder

``` bash
sudo apt install jq # install jq in case you don't already have it
```
``` bash
export BOT_NUMBER=+15551234567 # number you've obtained for your bot
export CAPTCHA=$(curl -s --data-binary "https://signalcaptchas.org/registration/generate.html" https://human-after-all-21.fly.dev/6LedYI0UAAAAAMt8HLj4s-_2M_nYOhWMMFRGYHgY | jq -r .solution.gRecaptchaResponse)
./signal-cli --config . -u $BOT_NUMBER register --captcha $CAPTCHA
```
You will receive an SMS with a 6 digit verification code. Use that code with the verify command to verify your phone number.

``` bash
./signal-cli --config . -u $BOT_NUMBER verify 000000
```

This will create a `data` directory that holds your Signal keys and secret data. DO NOT CHECK THIS DIRECTORY INTO VERSION CONTROL. You can use this `data` directory with signal-cli or auxin-cli. You can test that the registration and verification succeeded by sending yourself a message.

```bash
export ADMIN=+15551111111 #your personal signal number
./signal-cli --config . -u $BOT_NUMBER send $ADMIN -m "hello"
1641332354004
```
Signal-cli will output a timestamp and you should receive a message on your phone.

If your having trouble registering with this method, there's [a more thorough walkthrough on the signal-cli wiki](https://github.com/AsamK/signal-cli/wiki/Registration-with-captcha).

## Running Hellobot ##

If you've made it this far, the hard part is over, pat yourself on the back. Once you have a Signal data store, you can provision as many bots as you want with it (as long as only one runs at a time).

Hellobot is the simplest possible bot, it is a bot that replies to the message "/hello" with "hello, world". You can see the code for it in [sample_bots/hellobot](sample_bots/hellobot.py).

Hellobot will read environment variables from a secrets file. By default it looks for a file called dev_secrets. Create a file called dev_secrets with the following information on it (replace ADMIN with your personal number and BOT_NUMBER with the number you registered with signal-cli). DO NOT CHECK THIS FILE INTO VERSION CONTROL. Look at the end of the document for explanations of the other environment variables in the dev_secrets file.

```bash
ADMIN=+15551111111
BOT_NUMBER=+15551234567
NO_DOWNLOAD=1
NO_MEMFS=1
ROOT_DIR=.
SIGNAL=signal-cli
```

Finally you can run hellobot with
```bash
pipenv run python -m sample_bots.hellobot
```

You should see an output like this:

```
INFO utils:56: loading secrets from dev_secrets
INFO core:48: Using message parser: <class 'forest.message.StdioMessage'>
INFO payments_monitor:111: full-service url: http://localhost:9090/wallet
INFO datastore:96: SignalDatastore number is +15551234567
WARNING datastore:225: not setting up tmpdir, using current directory
INFO core:118: ['./signal-cli', '--trust-new-identities', 'always', '--config', '.', '--user', '+15551234567', 'jsonRpc']
======== Running on http://0.0.0.0:8080 ========
(Press CTRL+C to quit)
```

Now you can text your bot "/hello" and it should reply with "hello, world".

### Default forest commands ###

Every forest bot comes with some pre-packaged commands. You can test these with your hellobot. Try sending it "/printerfact" to learn a real fact about printers. `/help` will display all the available commands for any given bot. `/help [command]` will explain what the command does. The default commands are.

/help : prints available commands and information about a given command
/ping : replies with pong
/printerfact : prints a fact about printers

### Write your own bot! ###

You can add new commands by modifying hellobot.py. Notice the command structure "do_hello". Anything after do_ will become a slash command. Add the following command to hellobot.py and redeploy to see it in action:

```python
async def do_goodbye(self, message: Message) -> str:
        return "Goodbye, cruel world!"
```
And with that you've deployed your first forest bot. Congratulations!

## Next Steps and Further Information ##

This is just the beginning. The forest framework provides a lot more functionality, and there are a couple more complex bots in the repo as well. One of the main functionalities of forest bots is that it's easy to enable Signal Payments for them so that your users can pay your bot in $MOB. This allows your bot to collect donations or sell content. To learn about the payment functionalities, and build your first payments-enabled forest bot, check out [/echopay](/echopay).

### Storing your Signal keys

Forest provides a helper program to upload your data directory to a Postgres database. Once you have provisioned a database (e.g. via <http://supabase.com> or <https://fly.io/docs/reference/postgres>), add this line to your dev_secrets file.

```bash
DATABASE_URL=postgres://<your database url>
```

Then, you can upload your datastore with:

```bash
./forest/datastore.py upload --number $BOT_NUMBER --path .
```

## Options and secrets

These are the environment variables and flags that the bots read to work. Not all of them are necessary for every bot. As you saw hellobot only used a subset of these.

- `ENV`: if running locally, which {ENV}_secrets file to use. This is also optionally used as profile family name
- `BOT_NUMBER`: the number for the bot's signal account
- `ADMIN`: admin's phone number, primarily as a fallback recipient for invalid webhooks; may also be used to send error messages and metrics.
- `DATABASE_URL`: URL for the Postgres database to store the signal keys in as well as other information.
- `FULL_SERVICE_URL`: URL for [full-service](https://github.com/mobilecoinofficial/full-service) instance to use for sending and receiving payments
- `CLIENTCRT`: client certificate to connect to ssl-enabled full-service.
- `ROOTCRT`: certificate to validate full-service.
- `MNEMONIC`: account to import for full-service. Not Secure.
- `SIGNAL`: which signal client to use. can be 'signal-cli' for signal-cli or 'auxin' for auxin-cli.
- `ROOT_DIR`: specify the directory where the data file is stored, as well as where the signal-cli executable is.
- `SIGNAL_CLI_PATH`: specify where the signal-cli executable is if it is not in ROOT_DIR.
- `LOGLEVEL`: what log level to use for console logs (DEBUG, INFO, WARNING, ERROR).
- `TYPO_THRESHOLD`: maximum normalized Levenshtein edit distance for typo correction. 0 is only exact matches, 1 is any match. Default: 0.3

## Binary flags
- `NO_DOWNLOAD`: don't download a signal-cli datastore, instead use what's in the current working directory.
- `NO_MEMFS`: if this isn't set, MEMFS is started, making a fake filesystem in `./data` and used to upload the signal-cli datastore to the database whenever it is changed. If not `NO_DOWNLOAD`, also create an equivalent tmpdir at /tmp/local-signal, chdir to it, and symlink signal-cli process and avatar.
- `MONITOR_WALLET`: monitor transactions from full-service. Relevant only if you're giving users a payment address to send mobilecoin to instead of using signal pay.  Experimental, do not use.
- `LOGFILES`: create a debug.log.
- `ADMIN_METRICS`: send python and roundtrip timedeltas for each command to ADMIN.
- `ENABLE_MAGIC`: use string distence and expansions 

## Contributing

We accept Issues and Pull Requests. These are our style guides:

Code style: Ensure that `mypy *py` and `pylint *py` do not return errors before you push.

Use [black](https://github.com/psf/black) to format your python code. Prefer verbose, easier to read names over conciser ones.
