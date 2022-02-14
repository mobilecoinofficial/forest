from InquirerPy import inquirer
art = '''
                                  # #### ####
                                ### \/#|### |/####
                               ##\/#/ \||/##/_/##/_#
                             ###  \/###|/ \/ # ###
                           ##_\_#\_\## | #/###_/_####
                          ## #### # \ #| /  #### ##/##
                           __#_--###`  |{,###---###-~
                                     \ }{
                                      }}{
                                      }}{
                                 ejm  {{}
                                , -=-~{ .-^- _
                                      `}
                                       {'''

print(art)
print("Welcome to the Forest setup wizard!")

def main():
    menu = inquirer.select(
                message ="What would you like to do?",
                choices =[
                    "Set up a new bot.",
                    "Change settings ( such as bot phone number )",
                    "Deploy your bot on fly",
                ],
                default=None
            ).execute()

if __name__ == "__main__":
    main()

#name = inquirer.text(message="Please provide a number for your bot.").execute()
#confirm = inquirer.confirm(message="Confirm?").execute()
