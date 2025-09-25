#!/usr/bin/env python3
"""
Arquivo de entrada principal para deploy
"""

import os
import sys
import logging

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    """Função principal de entrada"""
    try:
        # Importar e executar o bot principal
        from main import main as bot_main
        bot_main()
    except ImportError as e:
        logging.error(f"Erro ao importar módulos: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Erro na execução: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()