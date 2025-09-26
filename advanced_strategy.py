# advanced_strategy.py

import asyncio
import logging
import traceback
import numpy as np
from decimal import Decimal
from time import time
from typing import Tuple, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False

try:
    from telegram import Bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

from config import config
from telegram_alert import TelegramAlert
from dex import DexClient, DexVersion
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor
from risk_manager import risk_manager
from discovery import subscribe_new_pairs, is_discovery_running
from utils import (
    escape_md_v2,
    get_token_balance,
    has_high_tax,
    is_contract_verified,
    is_token_concentrated,
    rate_limiter,
    configure_rate_limiter_from_config,
)

log = logging.getLogger("advanced_sniper")
configure_rate_limiter_from_config(config)

class SignalStrength(Enum):
    VERY_WEAK = 1
    WEAK = 2
    NEUTRAL = 3
    STRONG = 4
    VERY_STRONG = 5

@dataclass
class TechnicalIndicators:
    rsi: float
    volume_spike: float
    liquidity_growth: float
    price_momentum: float
    holder_distribution: float
    overall_score: float
    signal_strength: SignalStrength

@dataclass
class SniperConfig:
    min_liquidity: Decimal
    max_tax_bps: int
    min_volume_spike: float
    min_rsi_oversold: float
    max_rsi_overbought: float
    min_momentum_score: float
    take_profit_levels: List[float]  # Multiple TP levels
    stop_loss_pct: float
    trailing_stop_pct: float
    position_size_pct: float  # % of available balance
    max_positions: int
    min_signal_strength: SignalStrength

class AdvancedSniperStrategy:
    def __init__(self):
        # Web3 setup
        if WEB3_AVAILABLE:
            prov = Web3.HTTPProvider(config["RPC_URL"])
            self.w3 = Web3(prov)
            self.weth = Web3.to_checksum_address(config["WETH"])
        else:
            self.w3 = None
            self.weth = None
            log.warning("Web3 não disponível - funcionalidades blockchain limitadas")
        
        # Telegram setup
        if TELEGRAM_AVAILABLE and config.get("TELEGRAM_TOKEN"):
            self.bot = Bot(token=config["TELEGRAM_TOKEN"])
            self.alert = TelegramAlert(
                bot=self.bot,
                chat_id=config["TELEGRAM_CHAT_ID"]
            )
        else:
            self.bot = None
            self.alert = None
            log.warning("Telegram não disponível - alertas limitados")
        self.chat_id = config["TELEGRAM_CHAT_ID"]
        
        # Advanced configuration
        self.config = SniperConfig(
            min_liquidity=Decimal(str(config.get("MIN_LIQ_WETH", 1.0))),
            max_tax_bps=int(float(config.get("MAX_TAX_PCT", 8.0)) * 100),
            min_volume_spike=float(config.get("MIN_VOLUME_SPIKE", 2.0)),
            min_rsi_oversold=float(config.get("MIN_RSI_OVERSOLD", 30.0)),
            max_rsi_overbought=float(config.get("MAX_RSI_OVERBOUGHT", 70.0)),
            min_momentum_score=float(config.get("MIN_MOMENTUM_SCORE", 0.6)),
            take_profit_levels=[0.15, 0.30, 0.50, 1.0],  # 15%, 30%, 50%, 100%
            stop_loss_pct=float(config.get("STOP_LOSS_PCT", 0.08)),
            trailing_stop_pct=float(config.get("TRAIL_PCT", 0.05)),
            position_size_pct=float(config.get("POSITION_SIZE_PCT", 0.1)),
            max_positions=int(config.get("MAX_POSITIONS", 3)),
            min_signal_strength=SignalStrength.STRONG
        )
        
        # Active positions tracking
        self.active_positions: Dict[str, Dict] = {}
        self.price_history: Dict[str, List[Tuple[float, float]]] = {}  # token -> [(price, timestamp)]
        
        # Performance tracking
        self.total_trades = 0
        self.winning_trades = 0
        self.total_profit = Decimal('0')
        
    async def analyze_token_technical(self, dex: DexClient, pair: str, token: str) -> TechnicalIndicators:
        """Perform advanced technical analysis on token"""
        try:
            # Get price history (simulate with current price for now)
            current_price = dex.get_token_price(token, self.weth)
            if current_price is None:
                return TechnicalIndicators(0, 0, 0, 0, 0, 0, SignalStrength.VERY_WEAK)
            
            # Initialize price history if not exists
            if token not in self.price_history:
                self.price_history[token] = []
            
            # Add current price to history
            self.price_history[token].append((float(current_price), time()))
            
            # Keep only last 50 data points
            if len(self.price_history[token]) > 50:
                self.price_history[token] = self.price_history[token][-50:]
            
            # Calculate RSI (simplified)
            rsi = self._calculate_rsi(token)
            
            # Calculate volume spike
            volume_spike = self._calculate_volume_spike(dex, pair)
            
            # Calculate liquidity growth
            liquidity_growth = self._calculate_liquidity_growth(dex, pair)
            
            # Calculate price momentum
            price_momentum = self._calculate_price_momentum(token)
            
            # Analyze holder distribution
            holder_distribution = self._analyze_holder_distribution(token)
            
            # Calculate overall score
            overall_score = self._calculate_overall_score(
                rsi, volume_spike, liquidity_growth, price_momentum, holder_distribution
            )
            
            # Determine signal strength
            signal_strength = self._determine_signal_strength(overall_score)
            
            return TechnicalIndicators(
                rsi=rsi,
                volume_spike=volume_spike,
                liquidity_growth=liquidity_growth,
                price_momentum=price_momentum,
                holder_distribution=holder_distribution,
                overall_score=overall_score,
                signal_strength=signal_strength
            )
            
        except Exception as e:
            log.error(f"Error in technical analysis: {e}")
            return TechnicalIndicators(0, 0, 0, 0, 0, 0, SignalStrength.VERY_WEAK)
    
    def _calculate_rsi(self, token: str, period: int = 14) -> float:
        """Calculate RSI indicator"""
        if token not in self.price_history or len(self.price_history[token]) < period + 1:
            return 50.0  # Neutral RSI
        
        prices = [p[0] for p in self.price_history[token][-period-1:]]
        deltas = np.diff(prices)
        
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)
    
    def _calculate_volume_spike(self, dex: DexClient, pair: str) -> float:
        """Calculate volume spike indicator"""
        try:
            # Simulate volume analysis (in real implementation, would get actual volume data)
            # For now, return a random value between 0.5 and 3.0
            import random
            return random.uniform(0.5, 3.0)
        except:
            return 1.0
    
    def _calculate_liquidity_growth(self, dex: DexClient, pair: str) -> float:
        """Calculate liquidity growth rate"""
        try:
            # Simulate liquidity growth analysis
            import random
            return random.uniform(-0.2, 0.5)  # -20% to +50%
        except:
            return 0.0
    
    def _calculate_price_momentum(self, token: str) -> float:
        """Calculate price momentum score"""
        if token not in self.price_history or len(self.price_history[token]) < 5:
            return 0.0
        
        prices = [p[0] for p in self.price_history[token][-5:]]
        if len(prices) < 2:
            return 0.0
        
        # Simple momentum: (current - first) / first
        momentum = (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0
        return float(momentum)
    
    def _analyze_holder_distribution(self, token: str) -> float:
        """Analyze token holder distribution"""
        try:
            # Simulate holder analysis (would use actual blockchain data)
            import random
            return random.uniform(0.3, 0.9)  # 0.3 = concentrated, 0.9 = well distributed
        except:
            return 0.5
    
    def _calculate_overall_score(self, rsi: float, volume_spike: float, 
                               liquidity_growth: float, price_momentum: float, 
                               holder_distribution: float) -> float:
        """Calculate weighted overall score"""
        # Weights for different indicators
        weights = {
            'rsi': 0.2,
            'volume': 0.25,
            'liquidity': 0.2,
            'momentum': 0.2,
            'holders': 0.15
        }
        
        # Normalize RSI (30-70 range is good)
        rsi_score = 1.0 if 30 <= rsi <= 70 else max(0, 1 - abs(rsi - 50) / 50)
        
        # Volume spike score (higher is better, but cap at 3x)
        volume_score = min(volume_spike / 3.0, 1.0)
        
        # Liquidity growth score (positive growth is good)
        liquidity_score = max(0, min(liquidity_growth + 0.2, 1.0))
        
        # Momentum score (positive momentum is good, but not too extreme)
        momentum_score = max(0, min(price_momentum + 0.1, 1.0))
        
        # Holder distribution score (higher is better)
        holder_score = holder_distribution
        
        overall = (
            weights['rsi'] * rsi_score +
            weights['volume'] * volume_score +
            weights['liquidity'] * liquidity_score +
            weights['momentum'] * momentum_score +
            weights['holders'] * holder_score
        )
        
        return overall
    
    def _determine_signal_strength(self, score: float) -> SignalStrength:
        """Determine signal strength based on overall score"""
        if score >= 0.8:
            return SignalStrength.VERY_STRONG
        elif score >= 0.65:
            return SignalStrength.STRONG
        elif score >= 0.5:
            return SignalStrength.NEUTRAL
        elif score >= 0.35:
            return SignalStrength.WEAK
        else:
            return SignalStrength.VERY_WEAK
    
    def _is_memecoin_candidate(self, token: str, pair_data: dict) -> bool:
        """Detecta se um token é um candidato a memecoin promissor"""
        try:
            # Verificar configurações de memecoin
            min_liquidity = float(config.get('MEMECOIN_MIN_LIQUIDITY', 0.05))
            max_age_hours = int(config.get('MEMECOIN_MAX_AGE_HOURS', 24))
            min_holders = int(config.get('MEMECOIN_MIN_HOLDERS', 10))
            max_supply = int(config.get('MEMECOIN_MAX_SUPPLY', 1000000000))
            
            # Verificar blacklist de palavras
            blacklist = config.get('BLACKLIST_KEYWORDS', '').lower().split(',')
            token_name = pair_data.get('name', '').lower()
            token_symbol = pair_data.get('symbol', '').lower()
            
            for keyword in blacklist:
                if keyword.strip() and (keyword.strip() in token_name or keyword.strip() in token_symbol):
                    log.info(f"Token {token} rejeitado por blacklist: {keyword}")
                    return False
            
            # Verificar whitelist de padrões (bonus points)
            whitelist = config.get('WHITELIST_PATTERNS', '').lower().split(',')
            has_memecoin_pattern = False
            for pattern in whitelist:
                if pattern.strip() and (pattern.strip() in token_name or pattern.strip() in token_symbol):
                    has_memecoin_pattern = True
                    break
            
            # Verificar liquidez mínima
            liquidity_eth = pair_data.get('liquidity_eth', 0)
            if liquidity_eth < min_liquidity:
                log.debug(f"Token {token} rejeitado por baixa liquidez: {liquidity_eth}")
                return False
            
            # Verificar idade do token (se disponível)
            token_age_hours = pair_data.get('age_hours', 0)
            if token_age_hours > max_age_hours:
                log.debug(f"Token {token} rejeitado por idade: {token_age_hours}h")
                return False
            
            # Verificar supply máximo
            total_supply = pair_data.get('total_supply', 0)
            if total_supply > max_supply:
                log.debug(f"Token {token} rejeitado por supply alto: {total_supply}")
                return False
            
            # Verificar número mínimo de holders
            holder_count = pair_data.get('holder_count', 0)
            if holder_count < min_holders:
                log.debug(f"Token {token} rejeitado por poucos holders: {holder_count}")
                return False
            
            # Bonus para tokens com padrões de memecoin
            if has_memecoin_pattern:
                log.info(f"Token {token} tem padrão de memecoin: {token_name} / {token_symbol}")
                return True
            
            # Verificar volume 24h mínimo
            volume_24h = pair_data.get('volume_24h_usd', 0)
            min_volume = float(config.get('MIN_VOLUME_24H', 1000))
            if volume_24h < min_volume:
                log.debug(f"Token {token} rejeitado por baixo volume: ${volume_24h}")
                return False
            
            # Verificar market cap máximo
            market_cap = pair_data.get('market_cap_usd', 0)
            max_market_cap = float(config.get('MAX_MARKET_CAP', 10000000))
            if market_cap > max_market_cap:
                log.debug(f"Token {token} rejeitado por market cap alto: ${market_cap}")
                return False
            
            log.info(f"Token {token} passou nos filtros de memecoin")
            return True
            
        except Exception as e:
            log.error(f"Erro ao verificar memecoin {token}: {e}")
            return False
    
    async def should_enter_position(self, indicators: TechnicalIndicators, token: str = None, pair_data: dict = None) -> bool:
        """Determine if we should enter a position based on indicators"""
        # Check if we have room for more positions
        max_positions = int(config.get('MAX_POSITIONS', 3))
        if len(self.active_positions) >= max_positions:
            log.debug(f"Máximo de posições atingido: {len(self.active_positions)}/{max_positions}")
            return False
        
        # Verificar se é um candidato a memecoin promissor
        if token and pair_data:
            if not self._is_memecoin_candidate(token, pair_data):
                log.debug(f"Token {token} não passou nos filtros de memecoin")
                return False
            log.info(f"Token {token} é um candidato a memecoin promissor!")
        
        # Check signal strength
        if indicators.signal_strength.value < self.config.min_signal_strength.value:
            log.debug(f"Sinal fraco: {indicators.signal_strength.value} < {self.config.min_signal_strength.value}")
            return False
        
        # Check individual indicators
        checks = [
            indicators.volume_spike >= self.config.min_volume_spike,
            indicators.price_momentum >= self.config.min_momentum_score,
            indicators.overall_score >= 0.5,  # Reduzido para memecoins
            indicators.rsi >= self.config.min_rsi_oversold,
            indicators.rsi <= self.config.max_rsi_overbought
        ]
        
        passed_checks = sum(checks)
        log.info(f"Verificações passadas: {passed_checks}/5 para token {token}")
        
        # Para memecoins, ser mais flexível (3 de 5 checks)
        return passed_checks >= 3
    
    async def execute_smart_buy(self, dex: DexClient, pair: str, base: str, 
                              target: str, indicators: TechnicalIndicators) -> Optional[str]:
        """Execute buy with dynamic position sizing"""
        try:
            # Calculate position size based on signal strength
            base_size = float(self.config.position_size_pct)
            signal_multiplier = indicators.signal_strength.value / 3.0  # 0.33 to 1.67
            position_size = base_size * signal_multiplier
            
            # Get available balance
            balance = get_token_balance(
                ExchangeClient(router_address=dex.router), 
                base
            )
            
            if balance <= 0:
                return None
            
            # Calculate trade amount
            trade_amount = Decimal(str(balance * position_size))
            
            # Execute trade
            exch = ExchangeClient(router_address=dex.router)
            te = TradeExecutor(exchange_client=exch, dry_run=config["DRY_RUN"])
            safe = SafeTradeExecutor(executor=te, risk_manager=risk_manager)
            
            current_price = dex.get_token_price(target, base)
            slippage = dex.calc_dynamic_slippage(pair, float(trade_amount))
            
            tx_hash = safe.buy(
                token_in=base,
                token_out=target,
                amount_eth=trade_amount,
                current_price=current_price,
                last_trade_price=None,
                amount_out_min=None,
                slippage=slippage
            )
            
            if tx_hash:
                # Record position
                self.active_positions[target] = {
                    'pair': pair,
                    'entry_price': current_price,
                    'entry_time': time(),
                    'amount': trade_amount,
                    'highest_price': current_price,
                    'tp_levels_hit': 0,
                    'indicators': indicators
                }
                
                # Send detailed notification
                await self._send_buy_notification(target, tx_hash, indicators, trade_amount)
            
            return tx_hash
            
        except Exception as e:
            log.error(f"Error in smart buy: {e}")
            return None
    
    async def _send_buy_notification(self, token: str, tx_hash: str, 
                                   indicators: TechnicalIndicators, amount: Decimal):
        """Send detailed buy notification"""
        msg = (
            f"🎯 *COMPRA EXECUTADA*\n\n"
            f"Token: `{token}`\n"
            f"Valor: `{amount:.4f}` ETH\n"
            f"TX: `{tx_hash}`\n\n"
            f"📊 *Análise Técnica:*\n"
            f"• RSI: `{indicators.rsi:.1f}`\n"
            f"• Volume Spike: `{indicators.volume_spike:.2f}x`\n"
            f"• Momentum: `{indicators.price_momentum:.3f}`\n"
            f"• Score Geral: `{indicators.overall_score:.2f}`\n"
            f"• Força do Sinal: `{indicators.signal_strength.name}`\n\n"
            f"🎯 *Take Profits:* 15%, 30%, 50%, 100%\n"
            f"🛡️ *Stop Loss:* {self.config.stop_loss_pct*100:.1f}%"
        )
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=escape_md_v2(msg),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            log.error(f"Error sending buy notification: {e}")
    
    async def manage_positions(self):
        """Advanced position management with multiple TP levels"""
        for token, position in list(self.active_positions.items()):
            try:
                await self._manage_single_position(token, position)
            except Exception as e:
                log.error(f"Error managing position {token}: {e}")
    
    async def _manage_single_position(self, token: str, position: Dict):
        """Manage a single position with advanced exit strategy"""
        try:
            # Get current price
            dex = DexClient(self.w3, position['pair'])  # This needs to be fixed
            current_price = dex.get_token_price(token, self.weth)
            
            if current_price is None:
                return
            
            entry_price = position['entry_price']
            highest_price = position.get('highest_price', entry_price)
            
            # Update highest price
            if current_price > highest_price:
                position['highest_price'] = current_price
                highest_price = current_price
            
            # Calculate profit/loss
            profit_pct = (current_price - entry_price) / entry_price
            
            # Check take profit levels
            tp_levels = self.config.take_profit_levels
            current_tp_level = position.get('tp_levels_hit', 0)
            
            if current_tp_level < len(tp_levels):
                target_tp = tp_levels[current_tp_level]
                if profit_pct >= target_tp:
                    # Hit next TP level - sell portion
                    await self._execute_partial_sell(token, position, 0.25)  # Sell 25%
                    position['tp_levels_hit'] += 1
                    
                    await self._send_tp_notification(token, target_tp, current_tp_level + 1)
            
            # Check stop loss
            stop_loss_price = entry_price * (1 - self.config.stop_loss_pct)
            trailing_stop_price = highest_price * (1 - self.config.trailing_stop_pct)
            
            if current_price <= max(stop_loss_price, trailing_stop_price):
                # Stop loss triggered - sell all remaining
                await self._execute_full_sell(token, position)
                del self.active_positions[token]
                
                stop_type = "Stop Loss" if current_price <= stop_loss_price else "Trailing Stop"
                await self._send_stop_notification(token, stop_type, profit_pct)
            
        except Exception as e:
            log.error(f"Error in position management for {token}: {e}")
    
    async def _execute_partial_sell(self, token: str, position: Dict, percentage: float):
        """Execute partial sell of position"""
        try:
            # Get current balance
            balance = get_token_balance(
                ExchangeClient(router_address=position.get('router')), 
                token
            )
            
            if balance <= 0:
                return
            
            sell_amount = Decimal(str(balance * percentage))
            
            # Execute sell
            exch = ExchangeClient(router_address=position.get('router'))
            te = TradeExecutor(exchange_client=exch, dry_run=config["DRY_RUN"])
            safe = SafeTradeExecutor(executor=te, risk_manager=risk_manager)
            
            tx_hash = safe.sell(
                token_in=token,
                token_out=self.weth,
                amount_eth=sell_amount,
                current_price=None,  # Will be calculated
                last_trade_price=position['entry_price']
            )
            
            if tx_hash:
                self.total_trades += 1
                # Update position amount
                position['amount'] = position['amount'] * Decimal(str(1 - percentage))
            
        except Exception as e:
            log.error(f"Error in partial sell: {e}")
    
    async def _execute_full_sell(self, token: str, position: Dict):
        """Execute full sell of position"""
        try:
            balance = get_token_balance(
                ExchangeClient(router_address=position.get('router')), 
                token
            )
            
            if balance <= 0:
                return
            
            # Execute sell
            exch = ExchangeClient(router_address=position.get('router'))
            te = TradeExecutor(exchange_client=exch, dry_run=config["DRY_RUN"])
            safe = SafeTradeExecutor(executor=te, risk_manager=risk_manager)
            
            tx_hash = safe.sell(
                token_in=token,
                token_out=self.weth,
                amount_eth=Decimal(str(balance)),
                current_price=None,
                last_trade_price=position['entry_price']
            )
            
            if tx_hash:
                self.total_trades += 1
                # Calculate if this was a winning trade
                # This would need current price calculation
                
        except Exception as e:
            log.error(f"Error in full sell: {e}")
    
    async def _send_tp_notification(self, token: str, tp_level: float, tp_number: int):
        """Send take profit notification"""
        msg = f"💰 *TAKE PROFIT {tp_number}*\n\nToken: `{token}`\nNível: `{tp_level*100:.0f}%`\n25% da posição vendida!"
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=escape_md_v2(msg),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            log.error(f"Error sending TP notification: {e}")
    
    async def _send_stop_notification(self, token: str, stop_type: str, profit_pct: float):
        """Send stop loss notification"""
        msg = (
            f"🛡️ *{stop_type.upper()}*\n\n"
            f"Token: `{token}`\n"
            f"P&L: `{profit_pct*100:.2f}%`\n"
            f"Posição totalmente fechada!"
        )
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=escape_md_v2(msg),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            log.error(f"Error sending stop notification: {e}")
    
    def get_performance_stats(self) -> Dict:
        """Get performance statistics"""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'win_rate': win_rate,
            'total_profit': float(self.total_profit),
            'active_positions': len(self.active_positions),
            'max_positions': self.config.max_positions
        }

# Global instance (comentado para evitar inicialização automática)
# advanced_sniper = AdvancedSniperStrategy()