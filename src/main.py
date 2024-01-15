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

#Intantiate telegram bot instance
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
	bot.reply_to(message, "Silent MW has been started")
	api = UptimeKumaApi(KUMA_HOST)
	api.login(KUMA_LOGIN,KUMA_PASSWORD)
	api.resume_maintenance(KUMA_MW_ID)
	api.disconnect()

@bot.message_handler(commands=['stop'])
def send_welcome(message):
	bot.reply_to(message, "Silent MW has been completed")
	api = UptimeKumaApi(KUMA_HOST)
	api.login(KUMA_LOGIN,KUMA_PASSWORD)
	api.pause_maintenance(KUMA_MW_ID)
	api.disconnect()	

@bot.message_handler(commands=['start_mw'])
def send_welcome(message):
	bot.reply_to(message, "MW has been started. Sev1 chat has been notified")
	api = UptimeKumaApi(KUMA_HOST)
	api.login(KUMA_LOGIN,KUMA_PASSWORD)
	api.resume_maintenance(KUMA_MW_ID)
	api.disconnect()
	bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nMaintenance window has been started.  This may take awhile")

@bot.message_handler(commands=['stop_mw'])
def send_welcome(message):
	bot.reply_to(message, "MW has been completed. Sev1 chat has been notified")
	api = UptimeKumaApi(KUMA_HOST)
	api.login(KUMA_LOGIN,KUMA_PASSWORD)
	api.pause_maintenance(KUMA_MW_ID)
	api.disconnect()	
	bot.send_message(chat_id=CHAT_ID, text="NAS: Server Status \nMaintenance window has been completed")

bot.infinity_polling()
