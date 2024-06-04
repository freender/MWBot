import telebot
import cfg
from modules import modules

#Instantiate telegram bot
bot = telebot.TeleBot(cfg.TOKEN)
#bot = telebot.TeleBot(cfg.TOKEN_STAGING)

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
	if not modules.is_owner(message):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:
		result = modules.start_mw()
		bot.reply_to(message, result)	

@bot.message_handler(commands=['stop_silent'])
def command_stop_silent(message):
	if not modules.is_owner(message):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		result = modules.stop_mw()
		bot.reply_to(message, result)		

@bot.message_handler(commands=['firmware_mw'])
def command_firmware_mw(message):
	if not modules.is_owner(message):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		result = modules.start_mw()
		bot.reply_to(message, result + ". Sev1 chat has been notified")		
		bot.send_message(chat_id=cfg.CHAT_ID, text="NAS: Server Status \nFirmware update. \nETA - 15 minutes")

@bot.message_handler(commands=['reboot_mw'])
def command_reboot_mw(message):
	if not modules.is_owner(message):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		result = modules.start_mw()
		bot.reply_to(message, result + ". Sev1 chat has been notified")		
		bot.send_message(chat_id=cfg.CHAT_ID, text="NAS: Server Status \nNAS is going to be rebooted. \nETA - 10 minutes")

@bot.message_handler(commands=['generic_mw'])
def command_generic_mw(message):
	if not modules.is_owner(message):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		result = modules.start_mw()
		bot.reply_to(message, result + ". Sev1 chat has been notified")			
		bot.send_message(chat_id=cfg.CHAT_ID, text="NAS: Server Status \nMaintenance window has been started.  \nThis may take awhile")

@bot.message_handler(commands=['stop_mw'])
def command_stop_mw(message):
	if not modules.is_owner(message):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:	
		result = modules.stop_mw()
		bot.reply_to(message, result + ". Sev1 chat has been notified")
		bot.send_message(chat_id=cfg.CHAT_ID, text="NAS: Server Status \nMaintenance window has been completed")


@bot.message_handler(commands=['ip'])
def command_allow_cdn(message):
	if not modules.is_auth_user(message):
		bot.reply_to(message, "Sorry you are not allowed to use this command!")
	else:
		bot.send_chat_action(message.chat.id, 'typing')
		sent = bot.send_message(message.chat.id, "Send IP address")
		bot.register_next_step_handler(sent, ip)


def ip(message):
	ip=message.text
	if not modules.is_valid_ip(ip):
		bot.send_message(message.chat.id, "Invalid IP address format!\nDoublecheck and rerun /ip command")	
	else:	
		asn = modules.get_asn_from_ip(ip)
		if asn == "None":
			bot.send_message(message.chat.id, "ASN for this IP is not found!\nDoublecheck and rerun /ip command")		
		else:
			result = modules.add_asn_to_firewall_rule(asn)
			bot.send_message(message.chat.id, text=result)

@bot.message_handler(func=lambda message: modules.is_command(message.text))
def command_unknown(message):
    command = str(message.text).split()[0]
    bot.reply_to(
        message, "Sorry, {} command not found!\nPlease use /help to find all commands.".format(command))

bot.infinity_polling()
