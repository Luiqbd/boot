from web3 import Web3
from dotenv import load_dotenv
import os
import telebot

# Carrega variáveis do .env
load_dotenv()
RPC_URL = os.getenv("RPC_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Inicializa o bot
bot = telebot.TeleBot(BOT_TOKEN)

# Conecta à rede Base
web3 = Web3(Web3.HTTPProvider(RPC_URL))
if not web3.isConnected():
    raise Exception("Não foi possível conectar à rede Base")

# Sua carteira fixa
WALLET_ADDRESS = "0x3a94c149332d54481e9e956c4f38862b5329e52b947e7942a32463db1e192c56"

# Contrato correto do TOSHI na Base
TOKENS = {
    "TOSHI": {
        "address": "0xAC1Bd2486aAFB5C0fc3Fd868558b082a531B2B4",
        "decimals": 18
    }
}

# ABI mínima ERC-20
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }
]

# Função para consultar saldo
def get_wallet_balances() -> str:
    try:
        # ETH
        eth_balance = web3.eth.get_balance(WALLET_ADDRESS)
        formatted_eth = web3.fromWei(eth_balance, 'ether')

        # TOSHI
        token = TOKENS["TOSHI"]
        contract = web3.eth.contract(address=Web3.toChecksumAddress(token["address"]), abi=ERC20_ABI)
        raw_balance = contract.functions.balanceOf(WALLET_ADDRESS).call()
        formatted_toshi = raw_balance / (10 ** token["decimals"])

        return (
            f"💼 Carteira: {WALLET_ADDRESS}\n"
            f"🔹 ETH: {formatted_eth:.6f}\n"
            f"🔸 TOSHI: {formatted_toshi:.4f}"
        )
    except Exception as e:
        return f"❌ Erro ao consultar saldo: {str(e)}"

# Comando /wallet
@bot.message_handler(commands=['wallet'])
def wallet_handler(message):
    response = get_wallet_balances()
    bot.reply_to(message, response)

# Inicia o bot
bot.polling()
