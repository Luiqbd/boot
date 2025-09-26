#!/usr/bin/env python3
"""
Script para corrigir imports do web3 em todos os arquivos Python
"""

import os
import re

def fix_web3_imports(file_path):
    """Corrige imports do web3 em um arquivo"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Padrões para encontrar imports do web3
    patterns = [
        (r'from web3 import Web3\n', 'try:\n    from web3 import Web3\n    WEB3_AVAILABLE = True\nexcept ImportError:\n    WEB3_AVAILABLE = False\n    Web3 = None\n\n'),
        (r'from web3 import Web3, (.+)\n', r'try:\n    from web3 import Web3, \1\n    WEB3_AVAILABLE = True\nexcept ImportError:\n    WEB3_AVAILABLE = False\n    Web3 = None\n    # Definir outros imports como None ou Exception conforme necessário\n\n'),
        (r'from web3\.(.+) import (.+)\n', r'try:\n    from web3.\1 import \2\n    WEB3_AVAILABLE = True\nexcept ImportError:\n    WEB3_AVAILABLE = False\n    # Definir imports como None ou Exception conforme necessário\n\n')
    ]
    
    # Aplicar correções
    modified = False
    for pattern, replacement in patterns:
        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content)
            modified = True
    
    # Salvar se modificado
    if modified:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ Corrigido: {file_path}")
    else:
        print(f"⏭️  Sem alterações: {file_path}")

def main():
    """Corrige todos os arquivos Python no diretório atual"""
    files_to_fix = [
        'check_balance.py',
        'dex_client.py', 
        'discovery.py',
        'exchange_client.py',
        'exit_manager.py',
        'main.py',
        'send_tx.py',
        'strategy.py',
        'strategy_sniper.py',
        'test_sniper.py',
        'trade_executor.py',
        'wallet_check.py'
    ]
    
    for file_name in files_to_fix:
        if os.path.exists(file_name):
            fix_web3_imports(file_name)
        else:
            print(f"❌ Arquivo não encontrado: {file_name}")

if __name__ == "__main__":
    main()