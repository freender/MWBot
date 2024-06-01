import os
import json

#Read ENV Variables
TOKEN = os.environ['TOKEN']
TOKEN_STAGING = os.environ['TOKEN_STAGING']
CHAT_ID = os.environ['CHAT_ID']
OWNER = int(os.environ['OWNER'])
KUMA_HOST = os.environ['KUMA_HOST']
KUMA_LOGIN = os.environ['KUMA_LOGIN']
KUMA_PASSWORD = os.environ['KUMA_PASSWORD']
KUMA_MW_ID = os.environ['KUMA_MW_ID']
WAF_TOKEN  = os.environ['WAF_TOKEN']
WAF_ZONE  = os.environ['WAF_ZONE']
WAF_RULESET  = os.environ['WAF_RULESET']
WAF_RULEID  = os.environ['WAF_RULEID']
CDN_URL  = os.environ['CDN_URL']
TELEGRAM_AUTH_USERS = json.loads(os.environ['TELEGRAM_AUTH_USERS'])