# MWBot

Telegram BOT to start MW in Uptime Kuma and notify telegram chat

Docker Compose:
```
version: '3'

services:
  mwbot:
    image: mwbot:main
    container_name: mwbot
    environment:
      - TOKEN=AAA # Set telegram bot token
      - KUMA_HOST=http://IP:PORT # UptimeKuma IP address and port
      - KUMA_LOGIN=BBB # UptimeKuma Login
      - KUMA_PASSWORD=CCC # UptimeKuma Password
      - KUMA_MW_ID=3 # ID of MW you would like to start
      - CHAT_ID=DDD # Set telegram chat id
```
