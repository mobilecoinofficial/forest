To get familiarised with deploying and running a forest bot, we've provided a short tutorial to teach you how to deploy hellobot.

# High Level #

 * Install Pre-Requisites.
 * Install Signal-CLI to register an account.
 * Register an account with a phone number of your choice.
 * Determine datastore solution best for your application and upload credentials built with Signal-CLI.
 * Deploy your bot!

## Installing Prerequisites ##

### Python 3.9 ### 

Please refer to the [official Python wiki](https://wiki.python.org/moin/BeginnersGuide/Download) for instructions 
for instructions on installing Python 3.9 on your machine. On Debian/Ubuntu based systems one can simply run:

```
sudo apt install python3.9
sudo apt install python3-pip
```

### Dependencies ###

We use pipenv to handle dependencies, run:

```
python3.9 -m pip install pipenv
```
then 
```
pipenv install 
```
to install the prerequisites.

### Signal-Cli ###

#### From [Releases](https://github.com/AsamK/signal-cli/releases) ####

* With Java17 (openjdk-17-jre-headless:amd64) installed...
* Download the latest release tarball from https://github.com/AsamK/signal-cli/releases, ie) 
  * https://github.com/AsamK/signal-cli/releases/download/v0.10.0/signal-cli-0.10.0.tar.gz
* Extract it to a convenient working location, for example with `tar -xvf signal-cli-0.10.0.tar.gz`
* The signal-cli-0.10.0 folder has lib/ and bin/ directories. 
* Signal-CLI can now be invoked with `./signal-cli-0.10.0/bin/signal-cli`


#### Manually ####

To install Signal-Cli clone the official repo, cd into it, and build the binary. You will need Java 17 or greater. For more detailed instructions visit the [Signal-cli repository](https://github.com/AsamK/signal-cli). If your build is failing, first ensure that you're using a version of Java 17 or highuer with `java --version`

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

## Registering a new account

With a phone number from Google Voice, Forest Contact, Twilio, or Teli.net, a Signal account can be registered easily. These commands (bash compatible) serve as a starting point and use `human-after-all` as an alternative to manually solving the recaptcha challenge.

``` bash
export MY_PHONE_NUMBER=+15551234567
export CAPTCHA=$(curl -s --data-binary "https://signalcaptchas.org/registration/generate.html" https://human-after-all-21.fly.dev/6LedYI0UAAAAAMt8HLj4s-_2M_nYOhWMMFRGYHgY | jq -r .solution.gRecaptchaResponse)
signal-cli --config . -u $MY_PHONE_NUMBER --config state register --captcha $CAPTCHA
```


## Running Hellobot ##
