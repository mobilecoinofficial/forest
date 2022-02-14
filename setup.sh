#/bin/bash
echo "Installing dependencies.."
sudo apt update
sudo apt install python3.9 python3.9-dev python3-pip
python3.9 -m pip install pipenv
pipenv install
wget https://github.com/AsamK/signal-cli/releases/download/v0.10.3/signal-cli-0.10.3-Linux.tar.gz
tar -xvf signal-cli-0.10.3-Linux.tar.gz
ln -s ./signal-cli-0.10.3/bin/signal-cli .
pip3 install InquirerPy
python3 wizard.py

