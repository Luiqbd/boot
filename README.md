# Login App - Aplicativo Android com Autenticação

Um aplicativo Android com sistema de login/restrição de acesso.

## Funcionalidades

- ✅ Tela de login com usuário e senha
- ✅ Validação de campos obrigatórios
- ✅ Sessão persistente (lembra usuário logado)
- ✅ Tela inicial após login bem-sucedido
- ✅ Botão de logout
- ✅ Múltiplos usuários permitidos

## Usuários de Teste

O app vem com os seguintes usuários pré-cadastrados:

| Usuário | Senha |
|---------|-------|
| admin | 123456 |
| usuario | senha123 |
| teste | teste123 |

## Como Compilar

### Pré-requisitos

1. **Java JDK 17** - [Baixar](https://www.oracle.com/java/technologies/downloads/#java17)
2. **Android Studio** - [Baixar](https://developer.android.com/studio)
3. **SDK Android** (incluído no Android Studio)

### Passos para compilar no Android Studio

1. Abra o Android Studio
2. Selecione "Open an existing project"
3. Navegue até a pasta `LoginApp` e selecione
4. Aguarde o Gradle baixar as dependências
5. Vá em **Build** → **Build Bundle(s) / APK(s)** → **Build APK(s)**
6. O APK será gerado em: `app/build/outputs/apk/debug/app-debug.apk`

### Compilando via linha de comando

```bash
# Na pasta do projeto
cd LoginApp

# Compilar debug APK
./gradlew assembleDebug

# O APK estará em: app/build/outputs/apk/debug/app-debug.apk
```

## Estrutura do Projeto

```
LoginApp/
├── app/
│   ├── src/main/
│   │   ├── java/com/example/loginapp/
│   │   │   ├── MainActivity.kt        # Tela inicial
│   │   │   ├── LoginActivity.kt       # Tela de login
│   │   │   └── HomeActivity.kt        # Tela após login
│   │   ├── res/
│   │   │   ├── layout/                # Layouts XML
│   │   │   ├── values/                # Strings, cores, temas
│   │   │   └── drawable/              # Ícones
│   │   └── AndroidManifest.xml
│   └── build.gradle
├── build.gradle                       # Configuração do projeto
├── settings.gradle
└── gradle.properties
```

## Tecnologias Usadas

- **Kotlin** - Linguagem de programação
- **AndroidX** - Biblioteca de compatibilidade
- **Material Components** - Componentes de UI
- **ViewBinding** -绑定 de visualizações
- **SharedPreferences** - Armazenamento local

## Capturas de Tela

O app possui 3 telas:
1. **Tela Inicial** - Botão para ir ao login
2. **Tela de Login** - Campos de usuário e senha
3. **Tela Home** - Mensagem de boas-vindas após login

---

Desenvolvido com ❤️
