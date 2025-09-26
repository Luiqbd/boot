import os
try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
import requests

# Lê variáveis de ambiente
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if PRIVATE_KEY.startswith("0x"):
    PRIVATE_KEY = PRIVATE_KEY[2:]

# Gera endereço
address = Web3().eth.account.from_key(PRIVATE_KEY).address

# Monta mensagem para o Telegram
message = f"🔑 Endereço carregado no bot: {address}"

# Envia para o seu chat no Telegram
token = os.getenv("TELEGRAM_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")
url = f"https://api.telegram.org/bot{token}/sendMessage"
requests.post(url, data={"chat_id": chat_id, "text": message})

print("Mensagem enviada para o Telegram:", message)
