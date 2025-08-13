def sell(self):
    try:
        # Carrega contrato ERC20 do USDC
        with open("abis/erc20.json") as f:
            erc20_abi = json.load(f)
        usdc = self.web3.eth.contract(address=config["USDC"], abi=erc20_abi)

        # Define quanto vender
        amount_in = usdc.functions.allowance(self.address, config["DEX_ROUTER"]).call()
        if amount_in == 0:
            # Se não tiver aprovação, aprova tudo
            balance = usdc.functions.balanceOf(self.address).call()
            approve_tx = usdc.functions.approve(config["DEX_ROUTER"], balance).build_transaction({
                'from': self.address,
                'gas': 60000,
                'gasPrice': self.web3.to_wei('5', 'gwei'),
                'nonce': self.web3.eth.get_transaction_count(self.address),
                'chainId': config["CHAIN_ID"]
            })
            signed_approve = self.account.sign_transaction(approve_tx)
            approve_hash = self.web3.eth.send_raw_transaction(signed_approve.rawTransaction)
            print(f"✅ Aprovação enviada: {self.web3.to_hex(approve_hash)}")
            return

        # Executa venda USDC → ETH
        tx = self.router.functions.swapExactTokensForETH(
            amount_in,
            0,  # slippage mínima
            [config["USDC"], config["WETH"]],
            self.address,
            int(self.web3.eth.get_block('latest')['timestamp']) + config["TX_DEADLINE_SEC"]
        ).build_transaction({
            'from': self.address,
            'gas': 250000,
            'gasPrice': self.web3.to_wei('5', 'gwei'),
            'nonce': self.web3.eth.get_transaction_count(self.address),
            'chainId': config["CHAIN_ID"]
        })

        signed_tx = self.account.sign_transaction(tx)
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        print(f"✅ Venda enviada: {self.web3.to_hex(tx_hash)}")

    except Exception as e:
        print(f"❌ Erro ao vender: {e}")
