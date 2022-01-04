To get familiarised with deploying and running a forest bot, we've provided a short tutorial to teach you how to deploy hellobot.

# High Level #

 * Install Pre-Requisites.
 * Install Signal-CLI to register an account.
 * Register an account with a phone number of your choice.
 * Deploy the bot!

## Installing Prerequisites ##

### Python 3.9 ### 

Please refer to the [official Python wiki](https://wiki.python.org/moin/BeginnersGuide/Download) for instructions 
for instructions on installing Python 3.9 on your machine. On Debian/Ubuntu based systems one can simply run:

```
$ sudo apt install python3.9
$ sudo apt install python3-pip
```

### Dependencies ###

We use pipenv to handle dependencies, run:

```
$ python3.9 -m pip install pipenv
```
then 
```
$ pipenv install 
```
to install the prerequisites.

### Signal-Cli ###

[Signal-Cli](https://github.com/AsamK/signal-cli) is a command line interface for Signal. Forest bots run with Signal-Cli or [Auxin-cli](https://github.com/mobilecoinofficial/auxin-cli) as the backend. Auxin-cli is beta software, and does not yet allow to register a new phone number, so for this guide we will use Signal-Cli. 

To install or run Signal-Cli you will need Java 17 or greater. Verify that you have it installed by running:
```
$ java --version
openjdk 17.0.1 2021-10-19
OpenJDK Runtime Environment (build 17.0.1+12-Ubuntu-120.04)
OpenJDK 64-Bit Server VM (build 17.0.1+12-Ubuntu-120.04, mixed mode, sharing)
```

otherwise install with 
```
sudo apt install openjdk-17-jre-headless
```


#### From [Releases](https://github.com/AsamK/signal-cli/releases) ####

The maintainers of Signal-Cli provide precompiled releases you can download and run immediately.

Download and extract the latest release tarball from https://github.com/AsamK/signal-cli/releases 
```
$ wget https://github.com/AsamK/signal-cli/releases/download/v0.10.0/signal-cli-0.10.0.tar.gz
$ tar -xvf signal-cli-0.10.0.tar.gz
```
Verify the installation succeeded 

``` bash
$ ./signal-cli-0.10.0/bin/signal-cli --version
signal-cli 0.10.0
```

Finally for ease of use, link the executable to your working directory:

``` bash
$ ln -s ./signal-cli-0.10.0/bin/signal-cli .
$ ./signal-cli version
signal-cli 0.10.0
```

#### Building Signal-Cli from Source ####

You can also build Signal-Cli from source. You can do so by cloning the official repo and running `gradlew installDist`

``` bash
$ git clone https://github.com/AsamK/signal-cli.git

$ cd signal-cli

$ ./gradlew installDist
```
Verify the installation succeeded 

``` bash
$ ./build/install/signal-cli/bin/signal-cli --version
signal-cli 0.10.0
```

For more detailed instructions visit the [Signal-cli repository](https://github.com/AsamK/signal-cli). If your build is failing, first ensure that you're using a version of Java 17 or highuer with java --version

## Registering a Signal Account for your bot

You will need at least 2 signal accounts to properly test your bot. A signal account for the bot to run on and your own signal account to talk to the bot. To set up an additional signal account for your bot you can use a second phone or a VoIP service such as Google Voice, [Forest Contact](/contact), Twilio, or Teli.net. All you need is a phone number that can receive SMS.

We've deviced a shortcut to register a signal data store. Input your phone number with the country code (+1 for the US) and then run these commands to obtain a signal data store in a state folder

``` bash
$ export MY_BOT_NUMBER=+15551234567 # number you've obtained for your bot
$ export CAPTCHA=$(curl -s --data-binary "https://signalcaptchas.org/registration/generate.html" https://human-after-all-21.fly.dev/6LedYI0UAAAAAMt8HLj4s-_2M_nYOhWMMFRGYHgY | jq -r .solution.gRecaptchaResponse)
$ signal-cli --config . -u $MY_BOT_NUMBER register --captcha $CAPTCHA
```
You will receive an SMS with a 6 digit verification code. Use that code with the verify command to verify your phone number.

``` bash
$ ./signal-cli-release --config . -u $MY_BOT_NUMBER verify 000000
```

This will create a `data` directory that holds your signal keys and secret data. DO NOT CHECK THIS DIRECTORY INTO VERSION CONTROL. You can use this `data` directory with signal-cli or auxin-cli. You can test that the registration and verification succeeded by sending a message. 

```bash
$ export MY_ADMIN_NUMBER=+15551111111 #signal number from your phone
$ ./signal-cli --config . -u $MY_BOT_NUMBER send $MY_ADMIN_NUMBER -m "hello"
1641332354004
```
Signal-CLI will output a timestamp and you should receive a message on your phone.


## Running Hellobot ##

If you've made it this far, the hard part is over, pat yourself in the back. Once you have a Signal data store, you can provision as many bots as you want with it (as long as only one runs at a time).

Hellobot is the simplest possible bot, it is a bot that replies to the message "hello" with "hello, world". You can see the code for it in `/sample_bots/hellobot.py`


# Secrets

You can upload your signal keys and secret data to Postgres. Once you have a database (e.g. via <http://supabase.com> or <https://fly.io/docs/reference/postgres>), create a dev_secrets file with

```
DATABASE_URL=postgres://<your database url>
```

Then, you can upload your datastore with:

```bash
$ ./forest/datastore.py upload --number $MY_BOT_NUMBER --path .
```

# Options and secrets

- `ENV`: if running locally, which {ENV}_secrets file to use. this is also optionally used as profile family name
- `BOT_NUMBER`: signal account being used
- `ADMIN`: primarily fallback recipient for invalid webhooks; may also be used to send error messages and metrics
- `DATABASE_URL`: Postgres DB
- `FULL_SERVICE_URL`: url for full-service instance to use for sending and receiving payments
- `CLIENTCRT`: client certificate to connect to ssl-enabled full-service
- `ROOTCRT`: certificate to validate full-service
- `MNEMONIC`: account to import for full-service. insecure
- `SIGNAL`: which signal client to use. can be 'signal' for signal-cli or 'auxin' for auxin-cli

## Binary flags
- `NO_DOWNLOAD`: don't download a signal-cli datastore, instead use what's in the current working directory
- `NO_MEMFS`: if this isn't set, MEMFS is started, making a fake filesystem in `./data` and used to upload the signal-cli datastore to the database whenever it is changed. if not `NO_DOWNLOAD`, also create an equivalent tmpdir at /tmp/local-signal, chdir to it, and symlink signal-cli process and avatar
- `NO_MONITOR_WALLET`: monitor transactions from full-service. relevent only if you're giving users a payment address to send mobilecoin not with signal pay.  has bugs
- `SIGNAL_CLI_PATH`: executable to use. useful for running graalvm tracing agent
- `LOGFILES`: create a debug.log
- `LOGLEVEL`: what log level to use for console logs (DEBUG, INFO, WARNING, ERROR). 
- `ADMIN_METRICS`: send python and roundtrip timedeltas for each command to ADMIN

## Contributing

Code style: `mypy *py` and `pylint *py` should not have errors when you push. Run `black`. Prefer verbose, easier to read names over conciser ones.
