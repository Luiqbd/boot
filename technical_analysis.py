# technical_analysis.py

import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import logging
from decimal import Decimal

log = logging.getLogger("technical_analysis")

class TrendDirection(Enum):
    STRONG_BULLISH = 5
    BULLISH = 4
    NEUTRAL = 3
    BEARISH = 2
    STRONG_BEARISH = 1

@dataclass
class TechnicalSignal:
    indicator: str
    value: float
    signal: TrendDirection
    strength: float  # 0-1
    description: str

@dataclass
class MarketAnalysis:
    price: float
    volume: float
    liquidity: float
    signals: List[TechnicalSignal]
    overall_score: float
    trend_direction: TrendDirection
    confidence: float
    recommendation: str

class TechnicalAnalyzer:
    def __init__(self):
        self.price_history: Dict[str, List[Tuple[float, float, int]]] = {}  # token -> [(price, volume, timestamp)]
        self.min_data_points = 14  # Minimum data points for analysis
        
    def add_price_data(self, token: str, price: float, volume: float, timestamp: int):
        """Add price data point for a token"""
        if token not in self.price_history:
            self.price_history[token] = []
        
        self.price_history[token].append((price, volume, timestamp))
        
        # Keep only last 100 data points
        if len(self.price_history[token]) > 100:
            self.price_history[token] = self.price_history[token][-100:]
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate Relative Strength Index"""
        if len(prices) < period + 1:
            return 50.0  # Neutral RSI
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)
    
    def calculate_macd(self, prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float, float]:
        """Calculate MACD (Moving Average Convergence Divergence)"""
        if len(prices) < slow:
            return 0.0, 0.0, 0.0
        
        prices_array = np.array(prices)
        
        # Calculate EMAs
        ema_fast = self._calculate_ema(prices_array, fast)
        ema_slow = self._calculate_ema(prices_array, slow)
        
        # MACD line
        macd_line = ema_fast - ema_slow
        
        # Signal line (EMA of MACD)
        if len(prices) >= slow + signal:
            macd_values = []
            for i in range(slow - 1, len(prices)):
                ema_f = self._calculate_ema(prices_array[:i+1], fast)
                ema_s = self._calculate_ema(prices_array[:i+1], slow)
                macd_values.append(ema_f - ema_s)
            
            signal_line = self._calculate_ema(np.array(macd_values), signal)
        else:
            signal_line = 0.0
        
        # Histogram
        histogram = macd_line - signal_line
        
        return float(macd_line), float(signal_line), float(histogram)
    
    def _calculate_ema(self, prices: np.ndarray, period: int) -> float:
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return float(np.mean(prices))
        
        alpha = 2 / (period + 1)
        ema = prices[0]
        
        for price in prices[1:]:
            ema = alpha * price + (1 - alpha) * ema
        
        return float(ema)
    
    def calculate_bollinger_bands(self, prices: List[float], period: int = 20, std_dev: float = 2.0) -> Tuple[float, float, float]:
        """Calculate Bollinger Bands"""
        if len(prices) < period:
            avg_price = np.mean(prices)
            return avg_price, avg_price, avg_price
        
        recent_prices = prices[-period:]
        sma = np.mean(recent_prices)
        std = np.std(recent_prices)
        
        upper_band = sma + (std_dev * std)
        lower_band = sma - (std_dev * std)
        
        return float(upper_band), float(sma), float(lower_band)
    
    def calculate_volume_profile(self, volumes: List[float], period: int = 20) -> float:
        """Calculate volume profile score"""
        if len(volumes) < period:
            return 1.0
        
        recent_volumes = volumes[-period:]
        avg_volume = np.mean(recent_volumes)
        current_volume = volumes[-1]
        
        if avg_volume == 0:
            return 1.0
        
        volume_ratio = current_volume / avg_volume
        return float(min(volume_ratio, 10.0))  # Cap at 10x
    
    def calculate_momentum(self, prices: List[float], period: int = 10) -> float:
        """Calculate price momentum"""
        if len(prices) < period + 1:
            return 0.0
        
        current_price = prices[-1]
        past_price = prices[-period-1]
        
        if past_price == 0:
            return 0.0
        
        momentum = (current_price - past_price) / past_price
        return float(momentum)
    
    def calculate_volatility(self, prices: List[float], period: int = 20) -> float:
        """Calculate price volatility (standard deviation of returns)"""
        if len(prices) < period + 1:
            return 0.0
        
        recent_prices = prices[-period-1:]
        returns = np.diff(recent_prices) / recent_prices[:-1]
        volatility = np.std(returns)
        
        return float(volatility)
    
    def analyze_support_resistance(self, prices: List[float], period: int = 20) -> Tuple[float, float]:
        """Identify support and resistance levels"""
        if len(prices) < period:
            current_price = prices[-1] if prices else 0
            return current_price * 0.95, current_price * 1.05
        
        recent_prices = prices[-period:]
        support = min(recent_prices)
        resistance = max(recent_prices)
        
        return float(support), float(resistance)
    
    def generate_signals(self, token: str) -> List[TechnicalSignal]:
        """Generate technical analysis signals for a token"""
        if token not in self.price_history or len(self.price_history[token]) < self.min_data_points:
            return []
        
        data = self.price_history[token]
        prices = [d[0] for d in data]
        volumes = [d[1] for d in data]
        
        signals = []
        
        # RSI Signal
        rsi = self.calculate_rsi(prices)
        if rsi < 30:
            rsi_signal = TechnicalSignal(
                indicator="RSI",
                value=rsi,
                signal=TrendDirection.BULLISH,
                strength=min((30 - rsi) / 30, 1.0),
                description=f"RSI oversold at {rsi:.1f}"
            )
        elif rsi > 70:
            rsi_signal = TechnicalSignal(
                indicator="RSI",
                value=rsi,
                signal=TrendDirection.BEARISH,
                strength=min((rsi - 70) / 30, 1.0),
                description=f"RSI overbought at {rsi:.1f}"
            )
        else:
            rsi_signal = TechnicalSignal(
                indicator="RSI",
                value=rsi,
                signal=TrendDirection.NEUTRAL,
                strength=0.5,
                description=f"RSI neutral at {rsi:.1f}"
            )
        signals.append(rsi_signal)
        
        # MACD Signal
        macd_line, signal_line, histogram = self.calculate_macd(prices)
        if macd_line > signal_line and histogram > 0:
            macd_signal = TechnicalSignal(
                indicator="MACD",
                value=histogram,
                signal=TrendDirection.BULLISH,
                strength=min(abs(histogram) / 0.01, 1.0),
                description="MACD bullish crossover"
            )
        elif macd_line < signal_line and histogram < 0:
            macd_signal = TechnicalSignal(
                indicator="MACD",
                value=histogram,
                signal=TrendDirection.BEARISH,
                strength=min(abs(histogram) / 0.01, 1.0),
                description="MACD bearish crossover"
            )
        else:
            macd_signal = TechnicalSignal(
                indicator="MACD",
                value=histogram,
                signal=TrendDirection.NEUTRAL,
                strength=0.5,
                description="MACD neutral"
            )
        signals.append(macd_signal)
        
        # Bollinger Bands Signal
        upper_band, middle_band, lower_band = self.calculate_bollinger_bands(prices)
        current_price = prices[-1]
        
        if current_price < lower_band:
            bb_signal = TechnicalSignal(
                indicator="Bollinger Bands",
                value=current_price / lower_band,
                signal=TrendDirection.BULLISH,
                strength=min((lower_band - current_price) / lower_band, 1.0),
                description="Price below lower Bollinger Band"
            )
        elif current_price > upper_band:
            bb_signal = TechnicalSignal(
                indicator="Bollinger Bands",
                value=current_price / upper_band,
                signal=TrendDirection.BEARISH,
                strength=min((current_price - upper_band) / upper_band, 1.0),
                description="Price above upper Bollinger Band"
            )
        else:
            bb_signal = TechnicalSignal(
                indicator="Bollinger Bands",
                value=current_price / middle_band,
                signal=TrendDirection.NEUTRAL,
                strength=0.5,
                description="Price within Bollinger Bands"
            )
        signals.append(bb_signal)
        
        # Volume Signal
        volume_profile = self.calculate_volume_profile(volumes)
        if volume_profile > 2.0:
            vol_signal = TechnicalSignal(
                indicator="Volume",
                value=volume_profile,
                signal=TrendDirection.BULLISH,
                strength=min(volume_profile / 5.0, 1.0),
                description=f"High volume spike {volume_profile:.1f}x"
            )
        elif volume_profile < 0.5:
            vol_signal = TechnicalSignal(
                indicator="Volume",
                value=volume_profile,
                signal=TrendDirection.BEARISH,
                strength=min((1 - volume_profile) / 0.5, 1.0),
                description=f"Low volume {volume_profile:.1f}x"
            )
        else:
            vol_signal = TechnicalSignal(
                indicator="Volume",
                value=volume_profile,
                signal=TrendDirection.NEUTRAL,
                strength=0.5,
                description=f"Normal volume {volume_profile:.1f}x"
            )
        signals.append(vol_signal)
        
        # Momentum Signal
        momentum = self.calculate_momentum(prices)
        if momentum > 0.1:  # 10% momentum
            mom_signal = TechnicalSignal(
                indicator="Momentum",
                value=momentum,
                signal=TrendDirection.BULLISH,
                strength=min(momentum / 0.2, 1.0),
                description=f"Strong upward momentum {momentum*100:.1f}%"
            )
        elif momentum < -0.1:
            mom_signal = TechnicalSignal(
                indicator="Momentum",
                value=momentum,
                signal=TrendDirection.BEARISH,
                strength=min(abs(momentum) / 0.2, 1.0),
                description=f"Strong downward momentum {momentum*100:.1f}%"
            )
        else:
            mom_signal = TechnicalSignal(
                indicator="Momentum",
                value=momentum,
                signal=TrendDirection.NEUTRAL,
                strength=0.5,
                description=f"Weak momentum {momentum*100:.1f}%"
            )
        signals.append(mom_signal)
        
        return signals
    
    def analyze_token(self, token: str, current_liquidity: float = 0) -> Optional[MarketAnalysis]:
        """Perform comprehensive technical analysis on a token"""
        if token not in self.price_history or len(self.price_history[token]) < self.min_data_points:
            return None
        
        data = self.price_history[token]
        current_price = data[-1][0]
        current_volume = data[-1][1]
        
        # Generate all signals
        signals = self.generate_signals(token)
        
        # Calculate overall score
        bullish_signals = [s for s in signals if s.signal in [TrendDirection.BULLISH, TrendDirection.STRONG_BULLISH]]
        bearish_signals = [s for s in signals if s.signal in [TrendDirection.BEARISH, TrendDirection.STRONG_BEARISH]]
        
        bullish_score = sum(s.strength for s in bullish_signals)
        bearish_score = sum(s.strength for s in bearish_signals)
        total_signals = len(signals)
        
        if total_signals == 0:
            overall_score = 0.5
        else:
            overall_score = (bullish_score - bearish_score + total_signals) / (2 * total_signals)
            overall_score = max(0, min(1, overall_score))  # Clamp between 0 and 1
        
        # Determine trend direction
        if overall_score > 0.75:
            trend_direction = TrendDirection.STRONG_BULLISH
        elif overall_score > 0.6:
            trend_direction = TrendDirection.BULLISH
        elif overall_score > 0.4:
            trend_direction = TrendDirection.NEUTRAL
        elif overall_score > 0.25:
            trend_direction = TrendDirection.BEARISH
        else:
            trend_direction = TrendDirection.STRONG_BEARISH
        
        # Calculate confidence based on signal agreement
        signal_agreement = len([s for s in signals if s.signal == trend_direction]) / len(signals)
        confidence = signal_agreement * overall_score
        
        # Generate recommendation
        if trend_direction in [TrendDirection.STRONG_BULLISH, TrendDirection.BULLISH] and confidence > 0.6:
            recommendation = "STRONG BUY" if trend_direction == TrendDirection.STRONG_BULLISH else "BUY"
        elif trend_direction in [TrendDirection.STRONG_BEARISH, TrendDirection.BEARISH] and confidence > 0.6:
            recommendation = "STRONG SELL" if trend_direction == TrendDirection.STRONG_BEARISH else "SELL"
        else:
            recommendation = "HOLD"
        
        return MarketAnalysis(
            price=current_price,
            volume=current_volume,
            liquidity=current_liquidity,
            signals=signals,
            overall_score=overall_score,
            trend_direction=trend_direction,
            confidence=confidence,
            recommendation=recommendation
        )
    
    def get_analysis_summary(self, token: str) -> str:
        """Get a formatted summary of technical analysis"""
        analysis = self.analyze_token(token)
        if not analysis:
            return f"❌ Dados insuficientes para análise de {token[:10]}..."
        
        summary = f"📊 *Análise Técnica - {token[:10]}...*\n\n"
        summary += f"💰 Preço: `{analysis.price:.8f}` ETH\n"
        summary += f"📈 Volume: `{analysis.volume:.4f}`\n"
        summary += f"💧 Liquidez: `{analysis.liquidity:.4f}` ETH\n\n"
        
        summary += f"🎯 *Score Geral:* `{analysis.overall_score:.2f}`\n"
        summary += f"📊 *Tendência:* `{analysis.trend_direction.name}`\n"
        summary += f"🎪 *Confiança:* `{analysis.confidence:.2f}`\n"
        summary += f"💡 *Recomendação:* `{analysis.recommendation}`\n\n"
        
        summary += "*Sinais Técnicos:*\n"
        for signal in analysis.signals:
            emoji = "🟢" if signal.signal.value > 3 else "🔴" if signal.signal.value < 3 else "🟡"
            summary += f"{emoji} {signal.indicator}: {signal.description}\n"
        
        return summary

# Global instance
technical_analyzer = TechnicalAnalyzer()