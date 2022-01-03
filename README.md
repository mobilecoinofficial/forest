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

Signal-Cli is a command line interface for Signal. Forest bots run with Signal-Cli or Auxin-cli as the backend. Auxin-cli is alpha software, so for now we recommend you use Signal-Cli. 

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

```
$ ./signal-cli-0.10.0/bin/signal-cli --version
signal-cli 0.10.0
```

#### Building Signal-Cli from Source ####

You can also build Signal-Cli from source. You can do so by cloning the official repo and running `gradlew installDist`

```
git clone https://github.com/AsamK/signal-cli.git

cd signal-cli

./gradlew installDist
```
Verify the installation succeeded 

```
./build/install/signal-cli/bin/signal-cli --version
signal-cli 0.10.0
```


For more detailed instructions visit the [Signal-cli repository](https://github.com/AsamK/signal-cli). If your build is failing, first ensure that you're using a version of Java 17 or highuer with java --version

## Registering a Signal Account for your bot

You will need at least 2 signal accounts to properly test your bot. A signal account for the bot to run on and your own signal account to talk to the bot. To set up an additional signal account for your bot you can use a second phone or a VoIP service such as Google Voice, [Forest Contact](/contact), Twilio, or Teli.net. All you need is a phone number that can receive SMS.

With a phone number from Google Voice, Forest Contact, Twilio, or Teli.net, a Signal account can be registered easily. These commands (bash compatible) serve as a starting point and use `human-after-all` as an alternative to manually solving the recaptcha challenge.

``` bash
export MY_PHONE_NUMBER=+15551234567
export CAPTCHA=$(curl -s --data-binary "https://signalcaptchas.org/registration/generate.html" https://human-after-all-21.fly.dev/6LedYI0UAAAAAMt8HLj4s-_2M_nYOhWMMFRGYHgY | jq -r .solution.gRecaptchaResponse)
signal-cli --config . -u $MY_PHONE_NUMBER --config state register --captcha $CAPTCHA
```


## Running Hellobot ##
