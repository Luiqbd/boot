from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from web3 import Web3
from dotenv import load_dotenv
import os

# Carregar variáveis de ambiente
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Conectar à blockchain
web3 = Web3(Web3.HTTPProvider(RPC_URL))
address = web3.eth.account.from_key(PRIVATE_KEY).address

# Tokens que você quer consultar
TOKENS = {
    "USDC": {
        "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "decimals": 6
    },
    "TOSHI": {
        "address": "0xAE12C5930881c53715B369cec7606B70d8EB229f",
        "decimals": 18
    }
}

# ABI mínima para balanceOf
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

# Comando /wallet
async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eth_balance = web3.eth.get_balance(address)
    eth_formatted = web3.fromWei(eth_balance, 'ether')

    message = f"🔗 Endereço: `{address}`\n"
    message += f"💰 ETH: `{eth_formatted:.4f}` ETH\n"

    for name, token in TOKENS.items():
        contract = web3.eth.contract(address=Web3.to_checksum_address(token["address"]), abi=ERC20_ABI)
        raw_balance = contract.functions.balanceOf(address).call()
        formatted = raw_balance / (10 ** token["decimals"])
        message += f"💸 {name}: `{formatted:.4f}` {name}\n"

    await update.message.reply_text(message, parse_mode="Markdown")

# Inicializar bot
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("wallet", wallet))
app.run_polling()
