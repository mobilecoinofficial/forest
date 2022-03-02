```bash
sudo apt-get install python3.9
# if you're on hirsuite, you might have python3-pip --> python3.9 pip and don't need this
#curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
#python3.9 get-pip.py
pip install poetry
poetry install
poetry shell
# copy over your dev_secrets
cd ..
git clone https://github.com/forestcontact/signal-cli
sudo apt-get install default-jre
cd signal-cli 
./gradlew installDist
ln -s ~/signal-cli/build/install/signal-cli/bin/signal-cli  ../forest-draft/
cd ../forest-draft
python3.9 main.py
```
