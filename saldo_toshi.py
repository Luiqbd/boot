@bot.message_handler(commands=['saldo'])
def saldo_handler(message):
    try:
        # Contrato oficial do TOSHI na Base
        token_address = Web3.toChecksumAddress("0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4")
        decimals = 18

        # ABI mínima ERC-20
        contract = web3.eth.contract(address=token_address, abi=ERC20_ABI)
        raw_balance = contract.functions.balanceOf(WALLET_ADDRESS).call()
        formatted_balance = raw_balance / (10 ** decimals)

        # Resposta no Telegram
        response = (
            f"💰 Saldo de TOSHI\n"
            f"📍 Carteira: {WALLET_ADDRESS}\n"
            f"🪙 TOSHI: {formatted_balance:.4f}"
        )
        print("✅ Comando /saldo executado com sucesso")
        bot.reply_to(message, response)

    except Exception as e:
        print(f"❌ Erro ao comando /saldo: {str(e)}")
        bot.reply_to(message, f"❌ Erro ao consultar saldo: {str(e)}")
