import telebot
import os
from uptime_kuma_api import UptimeKumaApi

#Read ENV Variables
TOKEN = os.environ['TOKEN']
KUMA_HOST = os.environ['KUMA_HOST']
KUMA_LOGIN = os.environ['KUMA_LOGIN']
KUMA_PASSWORD = os.environ['KUMA_PASSWORD']
KUMA_MW_ID = os.environ['KUMA_MW_ID']
CHAT_ID = os.environ['CHAT_ID']
OWNER = os.environ['OWNER']

#Instantiate telegram bot
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def command_start(message):
    cid = message.chat.id
    bot.send_message(
        cid, "Welcome to freender_bot !\nType /help to find all commands.")


#@bot.message_handler(commands=['help'])
#def command_help(message):
#    cid = message.chat.id
#    help_text = "The following commands are available: \n"
#    for key in modules.COMMANDS:
#        help_text += '/' + key + ': '
#        help_text += modules.COMMANDS[key] + '\n'
#    bot.send_message(cid, help_text)

@bot.message_handler(commands=['start_silent'])
def send_welcome(message):
	cid = message.chat.id
	if message.chat.id != int(OWNER):
	    bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:
	    bot.reply_to(message, "Silent MW has been started")
	    api = UptimeKumaApi(KUMA_HOST)
	    api.login(KUMA_LOGIN,KUMA_PASSWORD)
	    api.resume_maintenance(KUMA_MW_ID)
	    api.disconnect()

@bot.message_handler(commands=['stop_silent'])
def send_welcome(message):
	if message.chat.id != int(OWNER):
	    bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
	    bot.reply_to(message, "Silent MW has been completed")
	    api = UptimeKumaApi(KUMA_HOST)
	    api.login(KUMA_LOGIN,KUMA_PASSWORD)
	    api.pause_maintenance(KUMA_MW_ID)
	    api.disconnect()	

@bot.message_handler(commands=['firmware_mw'])
def send_welcome(message):
	if message.chat.id != int(OWNER):
	    bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
	    bot.reply_to(message, "MW has been started. Sev1 chat has been notified")
	    api = UptimeKumaApi(KUMA_HOST)
	    api.login(KUMA_LOGIN,KUMA_PASSWORD)
	    api.resume_maintenance(KUMA_MW_ID)
	    api.disconnect()
	    bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nFirmware update has been started. \nETA - 15 minutes")

@bot.message_handler(commands=['reboot_mw'])
def send_welcome(message):
	if message.chat.id != int(OWNER):
	    bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
	    bot.reply_to(message, "MW has been started. Sev1 chat has been notified")
	    api = UptimeKumaApi(KUMA_HOST)
	    api.login(KUMA_LOGIN,KUMA_PASSWORD)
	    api.resume_maintenance(KUMA_MW_ID)
	    api.disconnect()
	    bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nNAS is going to be rebooted. \nETA - 10 minutes")

@bot.message_handler(commands=['start_mw'])
def send_welcome(message):
	if message.chat.id != int(OWNER):
	    bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
	    bot.reply_to(message, "MW has been completed. Sev1 chat has been notified")
	    api = UptimeKumaApi(KUMA_HOST)
	    api.login(KUMA_LOGIN,KUMA_PASSWORD)
	    api.resume_maintenance(KUMA_MW_ID)
	    api.disconnect()	
	    bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nMaintenance window has been started.  \nThis may take awhile")

@bot.message_handler(commands=['stop_mw'])
def send_welcome(message):
	if message.chat.id != int(OWNER):
	    bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
	    bot.reply_to(message, "MW has been completed. Sev1 chat has been notified")
	    api = UptimeKumaApi(KUMA_HOST)
	    api.login(KUMA_LOGIN,KUMA_PASSWORD)
	    api.pause_maintenance(KUMA_MW_ID)
	    api.disconnect()	
	    bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nMaintenance window has been completed")

@bot.message_handler(func=lambda message: modules.is_command(message.text))
def command_unknown(message):
    command = str(message.text).split()[0]
    bot.reply_to(
        message, "Sorry, {} command not found!\nPlease use /help to find all commands.".format(command))

bot.infinity_polling()
