import os
try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
from dotenv import load_dotenv
from telegram import Bot
from config import config
from telegram_alert import send_report
from web3.exceptions import TransactionNotFound

# Carrega variáveis do .env
load_dotenv()

# Conecta à rede Base
rpc_url = os.getenv("RPC_URL")
web3 = Web3(Web3.HTTPProvider(rpc_url))
if not web3.is_connected():
    print("❌ Não foi possível conectar à rede Base")
    exit(1)

# Chave privada e conta
private_key = os.getenv("PRIVATE_KEY")
account = web3.eth.account.from_key(private_key)
sender = account.address

# Inicializa o bot de notificações
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

def send_eth(recipient: str, amount_eth: float, gas: int = 21000, gas_price_gwei: float = 5, chain_id: int = 8453):
    """
    Envia ETH e garante que não haja saldo insuficiente.
    Em caso de erro, notifica via Telegram e retorna None.
    """
    # 1) Monta valores
    value = web3.to_wei(amount_eth, "ether")
    gas_price = web3.to_wei(gas_price_gwei, "gwei")
    nonce = web3.eth.get_transaction_count(sender)

    # 2) Verifica saldo
    balance = web3.eth.get_balance(sender)
    total_cost = value + gas * gas_price
    if total_cost > balance:
        msg = (
            f"⚠️ Saldo insuficiente:\n"
            f"   disponível = {web3.from_wei(balance, 'ether')} ETH\n"
            f"   necessário = {web3.from_wei(total_cost, 'ether')} ETH"
        )
        print(msg)
        send_report(bot_notify, msg)
        return None

    # 3) Prepara transação
    tx = {
        "to": recipient,
        "value": value,
        "gas": gas,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": chain_id,
    }

    # 4) Assina e envia
    try:
        signed = account.sign_transaction(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        tx_hex = web3.to_hex(tx_hash)
        msg = f"✅ Transação enviada: {tx_hex}"
        print(msg)
        send_report(bot_notify, msg)
        return tx_hex

    except ValueError as ve:
        # Geralmente usado pelo Web3 para erros de RPC ou insuficiência de fundos
        msg = f"❌ Falha ao enviar transação: {ve}"
        print(msg)
        send_report(bot_notify, msg)
        return None

    except Exception as e:
        # Catch-all para outros erros inesperados
        msg = f"❌ Erro inesperado no send_eth: {e}"
        print(msg)
        send_report(bot_notify, msg)
        return None

if __name__ == "__main__":
    print(f"🔗 Conectado como: {sender}")
    # Lê o destinatário do .env ou usa burn-address como default
    recipient = os.getenv("RECIPIENT", "0x000000000000000000000000000000000000dead")
    send_eth(recipient, 0.001)
