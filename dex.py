class DexClient:
    def __init__(self, rpc_url, private_key):
        self.rpc_url = rpc_url
        self.private_key = private_key

    def get_price(self):
        # Simulação de preço
        return 1.2

    def buy(self):
        print("Compra executada.")

    def sell(self):
        print("Venda executada.")