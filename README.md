To get familiarised with deploying and running a forest bot, we've provided a short tutorial to teach you how to deploy hellobot.

## Installing Prerequisites ##

### Python 3.9 ### 

Please refer to the [official Python wiki](https://wiki.python.org/moin/BeginnersGuide/Download) for instructions 
for instructions on installing Python 3.9 on your machine. On Debian/Ubuntu based systems you can simply run:

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

## regteri






## Running Hellobot ##
