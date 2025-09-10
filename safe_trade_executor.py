# safe_trade_executor.py

import logging
import inspect
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SafeTradeExecutor:
    """
    Envolve um TradeExecutor com:
      - checagem no RiskManager antes de cada ordem;
      - registro automático de resultados;
      - compatibilidade com diferentes assinaturas de can_trade;
      - retries básicos em caso de falha temporária.
    """

    def __init__(self, executor: Any, risk_manager: Any, max_retries: int = 3, retry_delay: float = 1.0):
        """
        executor: instância de TradeExecutor
        risk_manager: instância de RiskManager
        max_retries: número de tentativas em caso de erro ao enviar tx
        retry_delay: tempo (s) entre tentativas
        """
        self.executor = executor
        self.risk = risk_manager
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def _pode_negociar(
        self,
        current_price: float,
        last_trade_price: Optional[float],
        direction: str,
        amount_eth: float
    ) -> bool:
        """
        Chama risk.can_trade(...) suportando várias assinaturas:
        current_price, last_trade_price, direction, amount_eth (ou trade_size_eth).
        """
        try:
            sig = inspect.signature(self.risk.can_trade)
            kwargs = {}
            params = sig.parameters

            if "current_price" in params:
                kwargs["current_price"] = current_price
            if "last_trade_price" in params:
                kwargs["last_trade_price"] = last_trade_price
            if "direction" in params:
                kwargs["direction"] = direction
            # trade size
            if "amount_eth" in params:
                kwargs["amount_eth"] = amount_eth
            elif "trade_size_eth" in params:
                kwargs["trade_size_eth"] = amount_eth

            permitido = self.risk.can_trade(**kwargs)
            logger.debug(f"RiskManager.can_trade({kwargs}) -> {permitido}")
            return bool(permitido)
        except Exception as e:
            logger.error(f"Erro ao chamar RiskManager.can_trade: {e}", exc_info=True)
            # Em caso de erro de avaliação de risco, bloqueia para evitar prejuízo
            return False

    def _registrar_trade(
        self,
        direction: str,
        success: bool,
        tx_hash: Optional[str] = None,
        current_price: Optional[float] = None,
        amount_eth: Optional[float] = None
    ):
        """
        Encapsula chamadas de registro no RiskManager:
          - register_trade(success, direction, ...)
          - se disponível, envia preço ou amount
        """
        try:
            # registra o resultado básico
            self.risk.register_trade(success=success, direction=direction)
        except TypeError:
            # se assinatura antiga não usa direction
            self.risk.register_trade(success=success)
        except Exception as e:
            logger.error(f"Erro ao registrar trade no RiskManager: {e}", exc_info=True)

        # se tx_hash existir, tenta registrar no histórico interno
        if tx_hash:
            try:
                self.risk.record(
                    tipo=f"{direction}_{'success' if success else 'failed'}",
                    mensagem=f"{direction} {'sucesso' if success else 'falha'}",
                    pair=None,
                    token=None,
                    origem="SafeTradeExecutor",
                    tx_hash=tx_hash,
                    dry_run=getattr(self.risk, "dry_run", False)
                )
            except Exception:
                # ignora falhas adicionais de record
                pass

    def comprar(
        self,
        token_in: str,
        token_out: str,
        amount_eth: float,
        current_price: float,
        last_trade_price: Optional[float] = None,
        amount_out_min: Optional[int] = None,
        slippage: Optional[float] = None
    ) -> Optional[str]:
        """
        Executa ordem de compra:
          - checa risk.can_trade;
          - tenta até max_retries;
          - registra resultado.
        Retorna tx_hash ou None.
        """
        if not self._pode_negociar(current_price, last_trade_price, "buy", amount_eth):
            logger.info("Compra bloqueada pelo RiskManager")
            return None

        tx_hash = None
        for attempt in range(1, self.max_retries + 1):
            try:
                tx_hash = self.executor.buy(
                    token_in=token_in,
                    token_out=token_out,
                    amount_eth=amount_eth,
                    amount_out_min=amount_out_min,
                    slippage=slippage
                )
                logger.info(f"[BUY] tentativa {attempt} -> tx_hash={tx_hash}")
                break
            except Exception as e:
                logger.warning(f"[BUY] tentativa {attempt} falhou: {e}", exc_info=True)
                time.sleep(self.retry_delay)
        else:
            logger.error("Todas as tentativas de compra falharam")

        success = tx_hash is not None
        self._registrar_trade("buy", success, tx_hash, current_price, amount_eth)
        return tx_hash

    def vender(
        self,
        token_in: str,
        token_out: str,
        amount_eth: float,
        current_price: float,
        last_trade_price: Optional[float] = None,
        amount_out_min: Optional[int] = None,
        slippage: Optional[float] = None
    ) -> Optional[str]:
        """
        Executa ordem de venda:
          - checa risk.can_trade;
          - tenta até max_retries;
          - registra resultado.
        Retorna tx_hash ou None.
        """
        if not self._pode_negociar(current_price, last_trade_price, "sell", amount_eth):
            logger.info("Venda bloqueada pelo RiskManager")
            return None

        tx_hash = None
        for attempt in range(1, self.max_retries + 1):
            try:
                tx_hash = self.executor.sell(
                    token_in=token_in,
                    token_out=token_out,
                    amount_eth=amount_eth,
                    amount_out_min=amount_out_min,
                    slippage=slippage
                )
                logger.info(f"[SELL] tentativa {attempt} -> tx_hash={tx_hash}")
                break
            except Exception as e:
                logger.warning(f"[SELL] tentativa {attempt} falhou: {e}", exc_info=True)
                time.sleep(self.retry_delay)
        else:
            logger.error("Todas as tentativas de venda falharam")

        success = tx_hash is not None
        self._registrar_trade("sell", success, tx_hash, current_price, amount_eth)
        return tx_hash

    def registrar_prejuizo(self, loss_eth: float):
        """
        Registra prejuízo se o RiskManager suportar register_loss(loss_eth).
        """
        if loss_eth <= 0:
            return

        try:
            if hasattr(self.risk, "register_loss"):
                self.risk.register_loss(loss_eth)
                logger.debug(f"Registrado perda de {loss_eth} ETH no RiskManager")
        except Exception as e:
            logger.error(f"Erro ao registrar perda: {e}", exc_info=True)
