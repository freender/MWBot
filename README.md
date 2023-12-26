# MWBot

Telegram BOT to start MW in Uptime Kuma and notify telegram chat

Docker Compose:
```
version: '3'

services:
  mwbot:
    image: ghcr.io/freender/mwbot:main
    #build: https://github.com/freender/MWBot.git
    container_name: mwbot
    environment:
      - TOKEN=${TELEGRAM_TOKEN} # Set telegram bot token
      - KUMA_HOST=${UPTIME_HOST} # UptimeKuma IP address and port
      - KUMA_LOGIN=${UPTIME_LOGIN} # UptimeKuma Login
      - KUMA_PASSWORD=${UPTIME_PASSWORD} # UptimeKuma Password
      - KUMA_MW_ID=3 # ID of MW you would like to start
      - CHAT_ID=${TELEGRAM_CHATID} # Set telegram chat id
```
