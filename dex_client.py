# dex_client.py

from web3 import Web3
from typing import Optional

# ABI mínimo do UniswapV2 (getAmountsOut)
ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

class DexClient:
    """
    Cliente básico para interagir com um router UniswapV2:
      - get_token_price: retorna preço token → WETH
    """

    def __init__(self, web3: Web3, router_address: str):
        self.web3 = web3
        # garante checksum
        self.router_address = Web3.to_checksum_address(router_address)
        self.router = self.web3.eth.contract(
            address=self.router_address,
            abi=ROUTER_ABI
        )

    def get_token_price(
        self, token_address: str, weth_address: str
    ) -> Optional[float]:
        """
        Retorna o preço de 1 token → quantidade equivalente em WETH.
        Se falhar, retorna None.
        """
        token = Web3.to_checksum_address(token_address)
        weth  = Web3.to_checksum_address(weth_address)
        try:
            # sempre calcula preço de 1 token = 1e18 unidades
            amounts = self.router.functions.getAmountsOut(
                10**18, [token, weth]
            ).call()
            # resultado em wei de WETH; converte para float de WETH
            return amounts[-1] / 10**18
        except Exception:
            return None
