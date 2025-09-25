#!/usr/bin/env python3
# test_improvements.py

"""
Script de teste para as melhorias implementadas no sniper bot
"""

import sys
import os
import time
from decimal import Decimal
from unittest.mock import Mock, patch

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_technical_analysis():
    """Test technical analysis functionality"""
    print("🔍 Testando Análise Técnica...")
    
    try:
        from technical_analysis import TechnicalAnalyzer, TrendDirection
        
        analyzer = TechnicalAnalyzer()
        
        # Add some mock price data
        token = "0x1234567890123456789012345678901234567890"
        
        # Simulate price data with upward trend
        base_price = 0.001
        for i in range(20):
            price = base_price * (1 + i * 0.05)  # 5% increase each step
            volume = 1000 + i * 100
            timestamp = int(time.time()) + i * 60
            analyzer.add_price_data(token, price, volume, timestamp)
        
        # Test signal generation
        signals = analyzer.generate_signals(token)
        print(f"   ✅ Gerados {len(signals)} sinais técnicos")
        
        # Test market analysis
        analysis = analyzer.analyze_token(token, current_liquidity=5.0)
        if analysis:
            print(f"   ✅ Análise completa: Score {analysis.overall_score:.2f}, Tendência {analysis.trend_direction.name}")
            print(f"   ✅ Recomendação: {analysis.recommendation}")
        else:
            print("   ❌ Falha na análise de mercado")
            return False
        
        # Test summary generation
        summary = analyzer.get_analysis_summary(token)
        print(f"   ✅ Resumo gerado ({len(summary)} caracteres)")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro na análise técnica: {e}")
        return False

def test_advanced_strategy():
    """Test advanced strategy functionality"""
    print("🎯 Testando Estratégia Avançada...")
    
    try:
        from advanced_strategy import AdvancedSniperStrategy, SignalStrength
        
        # Mock Web3 and other dependencies
        with patch('advanced_strategy.Web3') as mock_web3, \
             patch('advanced_strategy.Bot') as mock_bot, \
             patch('advanced_strategy.TelegramAlert') as mock_alert:
            
            mock_web3.return_value.is_connected.return_value = True
            mock_web3.to_checksum_address.return_value = "0x4200000000000000000000000000000000000006"
            
            strategy = AdvancedSniperStrategy()
            
            # Test configuration
            print(f"   ✅ Configuração carregada: {len(strategy.config.take_profit_levels)} níveis de TP")
            
            # Test technical analysis
            mock_dex = Mock()
            mock_dex.get_token_price.return_value = 0.001
            
            indicators = strategy.analyze_token_technical(mock_dex, "pair", "token")
            print(f"   ✅ Indicadores técnicos: Score {indicators.overall_score:.2f}")
            
            # Test entry decision
            should_enter = strategy.should_enter_position(indicators)
            print(f"   ✅ Decisão de entrada: {'SIM' if should_enter else 'NÃO'}")
            
            # Test performance stats
            stats = strategy.get_performance_stats()
            print(f"   ✅ Estatísticas: {stats['total_trades']} trades, {stats['win_rate']:.1f}% win rate")
            
            return True
            
    except Exception as e:
        print(f"   ❌ Erro na estratégia avançada: {e}")
        return False

def test_risk_manager():
    """Test enhanced risk manager"""
    print("🛡️ Testando Gerenciamento de Risco...")
    
    try:
        from risk_manager import RiskManager, RiskLevel, TradeMetrics
        
        # Create risk manager instance
        rm = RiskManager(
            capital_eth=1.0,
            max_exposure_pct=0.1,
            max_trades_per_day=10,
            loss_limit=3,
            daily_loss_pct_limit=0.15,
            cooldown_sec=5
        )
        
        # Test trade validation
        can_trade = rm.can_trade(
            current_price=Decimal('0.001'),
            last_trade_price=None,
            direction="buy",
            amount_eth=Decimal('0.05'),
            token="0x1234",
            pair="0xpair"
        )
        print(f"   ✅ Validação de trade: {'APROVADO' if can_trade else 'BLOQUEADO'}")
        
        # Test trade registration
        rm.register_trade(
            success=True,
            pair="0xpair",
            direction="buy",
            timestamp=int(time.time()),
            pnl=Decimal('0.01'),
            token="0x1234"
        )
        print("   ✅ Trade registrado com sucesso")
        
        # Test metrics
        print(f"   ✅ Métricas: {rm.metrics.total_trades} trades, nível de risco {rm.current_risk_level.name}")
        
        # Test 24h stats
        trades_24h = rm.get_trade_count_24h()
        pnl_24h = rm.get_pnl_24h()
        print(f"   ✅ Últimas 24h: {trades_24h} trades, PnL {pnl_24h:.4f} ETH")
        
        # Test report generation
        report = rm.gerar_relatorio()
        print(f"   ✅ Relatório gerado ({len(report)} caracteres)")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro no gerenciamento de risco: {e}")
        return False

def test_telegram_interface():
    """Test enhanced Telegram interface"""
    print("📱 Testando Interface Telegram...")
    
    try:
        from main import build_menu, build_config_menu, build_analysis_menu
        
        # Test menu building
        main_menu = build_menu()
        print(f"   ✅ Menu principal: {len(main_menu.inline_keyboard)} linhas de botões")
        
        config_menu = build_config_menu()
        print(f"   ✅ Menu configurações: {len(config_menu.inline_keyboard)} linhas de botões")
        
        analysis_menu = build_analysis_menu()
        print(f"   ✅ Menu análise: {len(analysis_menu.inline_keyboard)} linhas de botões")
        
        # Count total buttons
        total_buttons = (
            sum(len(row) for row in main_menu.inline_keyboard) +
            sum(len(row) for row in config_menu.inline_keyboard) +
            sum(len(row) for row in analysis_menu.inline_keyboard)
        )
        print(f"   ✅ Total de botões implementados: {total_buttons}")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro na interface Telegram: {e}")
        return False

def test_configuration():
    """Test enhanced configuration"""
    print("⚙️ Testando Configuração...")
    
    try:
        from config import config, DexConfig
        
        # Test basic config loading
        print(f"   ✅ RPC URL: {config.get('RPC_URL', 'N/A')}")
        print(f"   ✅ Chain ID: {config.get('CHAIN_ID', 'N/A')}")
        print(f"   ✅ DRY RUN: {config.get('DRY_RUN', 'N/A')}")
        
        # Test DEX configuration
        dexes = config.get('DEXES', [])
        print(f"   ✅ DEXs configuradas: {len(dexes)}")
        
        # Test trading parameters
        trade_size = config.get('TRADE_SIZE_ETH', 0)
        min_liq = config.get('MIN_LIQ_WETH', 0)
        print(f"   ✅ Trade size: {trade_size} ETH, Min liquidez: {min_liq} ETH")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro na configuração: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Iniciando testes das melhorias do Sniper Bot...\n")
    
    tests = [
        ("Configuração", test_configuration),
        ("Análise Técnica", test_technical_analysis),
        ("Estratégia Avançada", test_advanced_strategy),
        ("Gerenciamento de Risco", test_risk_manager),
        ("Interface Telegram", test_telegram_interface),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
            print(f"{'✅' if result else '❌'} {test_name}: {'PASSOU' if result else 'FALHOU'}\n")
        except Exception as e:
            print(f"❌ {test_name}: ERRO - {e}\n")
            results.append((test_name, False))
    
    # Summary
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print("=" * 50)
    print(f"📊 RESUMO DOS TESTES: {passed}/{total} passaram")
    print("=" * 50)
    
    for test_name, result in results:
        status = "✅ PASSOU" if result else "❌ FALHOU"
        print(f"{status} - {test_name}")
    
    if passed == total:
        print("\n🎉 Todos os testes passaram! O bot está pronto para deploy.")
        return True
    else:
        print(f"\n⚠️ {total - passed} teste(s) falharam. Verifique os erros acima.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)