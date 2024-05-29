import telebot
import os
from modules import modules
from uptime_kuma_api import UptimeKumaApi

#Read ENV Variables
TOKEN = os.environ['TOKEN']
CHAT_ID = os.environ['CHAT_ID']
OWNER = os.environ['OWNER']

#Instantiate telegram bot
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def command_start(message):
    cid = message.chat.id
    bot.send_message(
        cid, "Welcome to freender_bot !\nType /help to find all commands. Your cid identifier is " + str(cid))

@bot.message_handler(commands=['help'])
def command_help(message):
	cid = message.chat.id
	help_text = "The following commands are available: \n"
	for key in modules.COMMANDS:
		help_text += '/' + key + ': '
		help_text += modules.COMMANDS[key] + '\n'
	bot.send_message(cid, help_text)

@bot.message_handler(commands=['start_silent'])
def command_start_silent(message):
	if message.chat.id != int(OWNER):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:
		bot.reply_to(message, "Silent MW has been started")
		modules.start_mw()

@bot.message_handler(commands=['stop_silent'])
def command_stop_silent(message):
	if message.chat.id != int(OWNER):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		bot.reply_to(message, "Silent MW has been completed")
		modules.stop_mw()

@bot.message_handler(commands=['firmware_mw'])
def command_firmware_mw(message):
	if message.chat.id != int(OWNER):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		bot.reply_to(message, "MW has been started. Sev1 chat has been notified")
		modules.start_mw()

@bot.message_handler(commands=['reboot_mw'])
def command_reboot_mw(message):
	if message.chat.id != int(OWNER):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		bot.reply_to(message, "MW has been started. Sev1 chat has been notified")
		modules.start_mw()
		bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nNAS is going to be rebooted. \nETA - 10 minutes")

@bot.message_handler(commands=['generic_mw'])
def command_generic_mw(message):
	if message.chat.id != int(OWNER):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		bot.reply_to(message, "MW has been completed. Sev1 chat has been notified")
		modules.start_mw()	
		bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nMaintenance window has been started.  \nThis may take awhile")

@bot.message_handler(commands=['stop_mw'])
def command_stop_mw(message):
	if message.chat.id != int(OWNER):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		bot.reply_to(message, "MW has been completed. Sev1 chat has been notified")
		modules.stop_mw()
		bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nMaintenance window has been completed")

@bot.message_handler(func=lambda message: modules.is_command(message.text))
def command_unknown(message):
    command = str(message.text).split()[0]
    bot.reply_to(
        message, "Sorry, {} command not found!\nPlease use /help to find all commands.".format(command))

bot.infinity_polling()
