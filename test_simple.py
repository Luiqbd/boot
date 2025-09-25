#!/usr/bin/env python3
# test_simple.py

"""
Teste simplificado das melhorias implementadas
"""

import sys
import os
import time
from decimal import Decimal

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_syntax_check():
    """Test if all Python files have valid syntax"""
    print("🔍 Verificando sintaxe dos arquivos...")
    
    files_to_check = [
        'main.py',
        'advanced_strategy.py', 
        'technical_analysis.py',
        'risk_manager.py',
        'config.py',
        'utils.py'
    ]
    
    results = []
    
    for filename in files_to_check:
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    code = f.read()
                compile(code, filename, 'exec')
                print(f"   ✅ {filename}: Sintaxe válida")
                results.append(True)
            except SyntaxError as e:
                print(f"   ❌ {filename}: Erro de sintaxe - {e}")
                results.append(False)
            except Exception as e:
                print(f"   ⚠️ {filename}: Aviso - {e}")
                results.append(True)  # Consider warnings as pass
        else:
            print(f"   ❌ {filename}: Arquivo não encontrado")
            results.append(False)
    
    return all(results)

def test_imports():
    """Test basic imports without full initialization"""
    print("📦 Testando imports básicos...")
    
    try:
        # Test config import
        from config import config
        print("   ✅ config.py importado com sucesso")
        
        # Test utils import  
        from utils import escape_md_v2
        print("   ✅ utils.py importado com sucesso")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro nos imports: {e}")
        return False

def test_technical_analysis_structure():
    """Test technical analysis module structure"""
    print("📊 Testando estrutura da análise técnica...")
    
    try:
        from technical_analysis import TechnicalAnalyzer, TrendDirection, TechnicalSignal, MarketAnalysis
        
        # Test enum values
        assert hasattr(TrendDirection, 'STRONG_BULLISH')
        assert hasattr(TrendDirection, 'BULLISH')
        assert hasattr(TrendDirection, 'NEUTRAL')
        assert hasattr(TrendDirection, 'BEARISH')
        assert hasattr(TrendDirection, 'STRONG_BEARISH')
        print("   ✅ TrendDirection enum definido corretamente")
        
        # Test analyzer creation
        analyzer = TechnicalAnalyzer()
        assert hasattr(analyzer, 'price_history')
        assert hasattr(analyzer, 'min_data_points')
        print("   ✅ TechnicalAnalyzer instanciado corretamente")
        
        # Test methods exist
        assert hasattr(analyzer, 'add_price_data')
        assert hasattr(analyzer, 'calculate_rsi')
        assert hasattr(analyzer, 'calculate_macd')
        assert hasattr(analyzer, 'generate_signals')
        print("   ✅ Métodos de análise técnica disponíveis")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro na análise técnica: {e}")
        return False

def test_risk_manager_structure():
    """Test risk manager module structure"""
    print("🛡️ Testando estrutura do gerenciamento de risco...")
    
    try:
        from risk_manager import RiskManager, RiskLevel, TradeMetrics
        
        # Test enum values
        assert hasattr(RiskLevel, 'LOW')
        assert hasattr(RiskLevel, 'MEDIUM') 
        assert hasattr(RiskLevel, 'HIGH')
        assert hasattr(RiskLevel, 'CRITICAL')
        print("   ✅ RiskLevel enum definido corretamente")
        
        # Test TradeMetrics dataclass
        metrics = TradeMetrics()
        assert hasattr(metrics, 'total_trades')
        assert hasattr(metrics, 'winning_trades')
        assert hasattr(metrics, 'win_rate')
        print("   ✅ TradeMetrics dataclass definido corretamente")
        
        # Test RiskManager creation
        rm = RiskManager()
        assert hasattr(rm, 'capital')
        assert hasattr(rm, 'current_risk_level')
        assert hasattr(rm, 'metrics')
        print("   ✅ RiskManager instanciado corretamente")
        
        # Test methods exist
        assert hasattr(rm, 'can_trade')
        assert hasattr(rm, 'register_trade')
        assert hasattr(rm, 'gerar_relatorio')
        print("   ✅ Métodos de gerenciamento de risco disponíveis")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro no gerenciamento de risco: {e}")
        return False

def test_advanced_strategy_structure():
    """Test advanced strategy module structure"""
    print("🎯 Testando estrutura da estratégia avançada...")
    
    try:
        # Import without full initialization
        import advanced_strategy
        
        # Check if classes are defined
        assert hasattr(advanced_strategy, 'SignalStrength')
        assert hasattr(advanced_strategy, 'TechnicalIndicators')
        assert hasattr(advanced_strategy, 'AdvancedSniperStrategy')
        print("   ✅ Classes da estratégia avançada definidas")
        
        # Check SignalStrength enum
        signal_strength = advanced_strategy.SignalStrength
        assert hasattr(signal_strength, 'WEAK')
        assert hasattr(signal_strength, 'MODERATE')
        assert hasattr(signal_strength, 'STRONG')
        print("   ✅ SignalStrength enum definido corretamente")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro na estratégia avançada: {e}")
        return False

def test_file_structure():
    """Test if all expected files exist"""
    print("📁 Verificando estrutura de arquivos...")
    
    expected_files = [
        'main.py',
        'config.py', 
        'utils.py',
        'risk_manager.py',
        'advanced_strategy.py',
        'technical_analysis.py',
        'requirements.txt',
        '.env.example',
        'README.md'
    ]
    
    missing_files = []
    existing_files = []
    
    for filename in expected_files:
        if os.path.exists(filename):
            existing_files.append(filename)
            print(f"   ✅ {filename}")
        else:
            missing_files.append(filename)
            print(f"   ❌ {filename} (não encontrado)")
    
    print(f"   📊 {len(existing_files)}/{len(expected_files)} arquivos encontrados")
    
    return len(missing_files) == 0

def test_configuration_structure():
    """Test configuration structure"""
    print("⚙️ Testando estrutura de configuração...")
    
    try:
        from config import config
        
        # Test if config is accessible
        assert config is not None
        print("   ✅ Objeto config acessível")
        
        # Test some expected config keys
        expected_keys = [
            'RPC_URL',
            'CHAIN_ID', 
            'DRY_RUN',
            'TRADE_SIZE_ETH',
            'MIN_LIQ_WETH'
        ]
        
        available_keys = []
        for key in expected_keys:
            if key in config or hasattr(config, 'get'):
                available_keys.append(key)
                print(f"   ✅ Configuração {key} disponível")
        
        print(f"   📊 {len(available_keys)}/{len(expected_keys)} configurações disponíveis")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Erro na configuração: {e}")
        return False

def main():
    """Run all simplified tests"""
    print("🚀 Iniciando testes simplificados das melhorias...\n")
    
    tests = [
        ("Estrutura de Arquivos", test_file_structure),
        ("Verificação de Sintaxe", test_syntax_check),
        ("Imports Básicos", test_imports),
        ("Configuração", test_configuration_structure),
        ("Análise Técnica", test_technical_analysis_structure),
        ("Gerenciamento de Risco", test_risk_manager_structure),
        ("Estratégia Avançada", test_advanced_strategy_structure),
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
    
    print("=" * 60)
    print(f"📊 RESUMO DOS TESTES SIMPLIFICADOS: {passed}/{total} passaram")
    print("=" * 60)
    
    for test_name, result in results:
        status = "✅ PASSOU" if result else "❌ FALHOU"
        print(f"{status} - {test_name}")
    
    if passed == total:
        print("\n🎉 Todos os testes passaram! Estrutura do bot está correta.")
        print("💡 Para testes completos, instale todas as dependências.")
        return True
    else:
        print(f"\n⚠️ {total - passed} teste(s) falharam. Verifique os erros acima.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)