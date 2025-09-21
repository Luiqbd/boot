# utils.py

import re
import logging
import requests
from collections import deque
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Callable, Deque, Dict, List, Optional, Union

from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ABIFunctionNotFound, ContractLogicError

from config import config

logger = logging.getLogger(__name__)

def escape_md_v2(text: str) -> str:
    """
    Escapa caracteres especiais para Telegram MarkdownV2.
    Caracteres escapados: _ * [ ] ( ) ~ ` > # + = - | { } . ! \
    """
    # Coloque o hífen logo após o = para não interferir no range do caractere
    pattern = r'([_*

\[\]

()~`>#+=\-|{}.!\\]

)'
    return re.sub(pattern, r'\\\1', text)

# Ratelimiter e funções de Etherscan omissas para brevidade…
