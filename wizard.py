import asyncio
from contextlib import redirect_stdout
from email.policy import default
from fileinput import filename
from random import choice
from InquirerPy import inquirer, prompt_async, base, validator, prompt
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator
import rich
from rich.console import Console
from time import sleep
import os
import subprocess
import shutil
import urllib.request
import random



tree = '''
                                                            	@@@@.................................@@@
   ad88                                                     	@.......................................
  d8"                                                ,d     	...........%%..............%%%..........
  88                                                 88     	...........%%..............%%%..........
MM88MMM ,adPPYba,  8b,dPPYba,  ,adPPYba, ,adPPYba, MM88MMM  	...........%%..............%%%..........
  88   a8"     "8a 88P'   "Y8 a8P_____88 I8[    ""   88     	....%%%%%%%%%%%%%%%.%%%%%%%%%%%%%%%%%...
  88   8b       d8 88         8PP"""""""  `"Y8ba,    88     	..........%%%%............%%%%%.........
  88   "8a,   ,a8" 88         "8b,   ,aa aa    ]8I   88,	.........%%%%%%%.........%%%%%%%........
  88    `"YbbdP"'  88          `"Ybbd8"' `"YbbdP"'   "Y888 	........%%.%%..%%%......%%*%%%%%%.......
								......#%%..%%....%/....%%..%%%.%%%......
								.....%%%...%%........%%%...%%%..%%%.....
								...%%%,....%%......%%%#....%%%....%%%...
								....%......%%.....%%%......%%%.....%%...
								...........%%..............%%%..........
								...........%%..............%%%..........
								@.......................................
'''


style = "green"

console = Console()
tasks = [f"task {n}" for n in range(1, 11)]

console.print(tree,style=style)


def main():
    menu = inquirer.select(
        message="Welcome to the forest setup wizard.", 
        choices=[
            Choice(value=do_newbot, name="Make a new bot.", enabled=True), 
            Choice(value=settings, name="Settings"), 
            Choice(value=do_update, name="Update"),
            Choice(value=do_deps, name="Install Dependencies"),
            Choice(value=do_exit, name="Exit")],default=None).execute()
    menu()

def settings():
    secrets = open("dev_secrets", "w+")
    current_secrets = secrets.read()
    pref = inquirer.select(
        message="What would you like to do?",
        choices=[
            Choice(value=do_number, name="Change bot number"),
            Choice(value=do_auxin, name="Switch to auxin"),
            Choice(value=do_rust, name="Set up Rust for Auxin", enabled=True),
            Choice(value=do_signalcli, name="Switch to Signal-Cli")],default=None).execute()
    pref()


def do_auxin():
    return "auxin"
#    auxins = inquirer.select(
#        message="Do you have auxin already?"
#        choices=[
#            Choice(value=build_auxin, name="No, build Auxin for me using cargo",enabled=True),
#            Choice(value=switch_auxin, name="I have auxin, just change the parameter in 'dev_secrets'"),],default=None).execute()
#    auxins()

def do_number():
    return "number stuff here"

def do_rust():
    with console.status("[bold green]Setting up rust 'sh rust.sh'...") as status:
        while tasks:
            task = tasks.pop(0)
            get_rust()
            os.system("sh rust.sh")
        
def get_rust():
    return subprocess.run("curl -o rust.sh --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs", shell=True)

def build_auxin():
    os.system('git clone https://github.com/mobilecoinofficial/auxin.git')
    os.system('rustup default nightly')
 
def do_update():
    with console.status("[bold green]git pull") as status:
        while tasks:
            return os.system('git pull')


def do_signalcli():
    with console.status("[bold green]Setting up rust...") as status:  
            return "signal cli"
    
def do_newbot():
    newbot = inquirer.select(
        message="What template would you like to start with?",
        choices=[
            Choice(value=do_hellobot, name="HelloBot"),]).execute()
    print(newbot())

def do_hellobot():
    return "hellobot"

def do_deps(): 
    bashdeps()

def bashdeps():
    os.system('sh setup.sh')

def do_exit():
    exit()



if __name__ == "__main__":
    main()


