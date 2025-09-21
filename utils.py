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
    Escapa caracteres especiais para MarkdownV2.
    """
    return re.sub(r'([._\\\-\\*\

\[\\]

\\(\\)~`>#+=|{}.!])', r'\\\1', text)

# Ratelimiter e Etherscan omitted para brevidade
