# dex_client.py

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None

from typing import Optional

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
    Cliente para UniswapV2‐style routers: getAmountsOut → preço token→WETH.
    """

    def __init__(self, web3: Web3, router_address: str):
        self.web3 = web3
        self.router = self.web3.eth.contract(
            address=Web3.to_checksum_address(router_address),
            abi=ROUTER_ABI
        )

    def get_token_price(
        self,
        token_address: str,
        weth_address: str
    ) -> Optional[float]:
        token = Web3.to_checksum_address(token_address)
        weth  = Web3.to_checksum_address(weth_address)
        try:
            amounts = self.router.functions.getAmountsOut(
                10**18, [token, weth]
            ).call()
            return amounts[-1] / 10**18
        except Exception:
            return None
