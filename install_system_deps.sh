#!/bin/bash
# Instala dependências DE SISTEMA (não-Python) necessárias para o news-app.
#
# Uso:
#   chmod +x install_system_deps.sh
#   sudo ./install_system_deps.sh
#
# Suporta: Rocky Linux 9, RHEL 9, CentOS Stream 9, Fedora.
#          (Para Ubuntu/Debian, use o bloco apt comentado no final.)

set -e

echo "======================================="
echo "  News-App — Instalação de Deps do SO"
echo "======================================="
echo ""

# Detecta gerenciador de pacotes
if command -v dnf > /dev/null 2>&1; then
    PKG_MGR="dnf"
elif command -v yum > /dev/null 2>&1; then
    PKG_MGR="yum"
elif command -v apt > /dev/null 2>&1; then
    PKG_MGR="apt"
else
    echo "❌ Gerenciador de pacotes não detectado. Use Rocky/RHEL/Fedora/Ubuntu."
    exit 1
fi

echo "📦 Gerenciador de pacotes: $PKG_MGR"
echo ""

# ─── 1. Google Chrome (para TikTok via Selenium headless) ────────────────────
echo "🌐 [1/3] Verificando Google Chrome..."
if command -v google-chrome > /dev/null 2>&1; then
    VERSAO=$(google-chrome --version)
    echo "   ✅ Já instalado: $VERSAO"
else
    echo "   ⏳ Instalando Google Chrome..."

    if [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
        # Rocky 9 / RHEL 9 / Fedora
        sudo tee /etc/yum.repos.d/google-chrome.repo > /dev/null <<'EOF'
[google-chrome]
name=google-chrome
baseurl=https://dl.google.com/linux/chrome/rpm/stable/x86_64
enabled=1
gpgcheck=1
gpgkey=https://dl.google.com/linux/linux_signing_key.pub
EOF
        sudo $PKG_MGR install -y google-chrome-stable
    elif [ "$PKG_MGR" = "apt" ]; then
        # Ubuntu / Debian
        sudo apt update
        sudo apt install -y wget
        wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
        sudo sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list'
        sudo apt update
        sudo apt install -y google-chrome-stable
    fi

    if command -v google-chrome > /dev/null 2>&1; then
        VERSAO=$(google-chrome --version)
        echo "   ✅ Instalado com sucesso: $VERSAO"
    else
        echo "   ❌ Falha na instalação do Chrome"
        exit 1
    fi
fi

# ─── 2. ffmpeg (opcional — fallback via imageio-ffmpeg do pip já cobre) ──────
echo ""
echo "🎬 [2/3] Verificando ffmpeg do sistema (opcional)..."
if command -v ffmpeg > /dev/null 2>&1; then
    VERSAO_FF=$(ffmpeg -version | head -1)
    echo "   ✅ Já instalado: $VERSAO_FF"
else
    echo "   ℹ️  Não instalado — não é crítico (moviepy usa imageio-ffmpeg do pip)."
    echo "   Para instalar mesmo assim:"
    if [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
        echo "     sudo $PKG_MGR config-manager --set-enabled crb"
        echo "     sudo $PKG_MGR install -y ffmpeg-free"
    elif [ "$PKG_MGR" = "apt" ]; then
        echo "     sudo apt install -y ffmpeg"
    fi
fi

# ─── 3. Teste headless ───────────────────────────────────────────────────────
echo ""
echo "🧪 [3/3] Testando Chrome em modo headless (sem GUI)..."
if google-chrome --headless --disable-gpu --no-sandbox --dump-dom https://google.com 2>/dev/null | grep -q "<html"; then
    echo "   ✅ Chrome headless funcionando — TikTok upload deve funcionar"
else
    echo "   ⚠️  Chrome instalado mas teste headless falhou"
    echo "       Talvez precise de libs gráficas extras. Tente:"
    if [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
        echo "         sudo $PKG_MGR install -y libxshmfence libxcomposite libxdamage libxrandr alsa-lib"
    elif [ "$PKG_MGR" = "apt" ]; then
        echo "         sudo apt install -y libxshmfence1 libxcomposite1 libxdamage1 libxrandr2 libasound2"
    fi
fi

echo ""
echo "======================================="
echo "  ✅ Instalação concluída"
echo "======================================="
echo ""
echo "Próximos passos:"
echo "  1. Ative o venv:  source .venv/bin/activate"
echo "  2. Pacotes Python: pip install -r requirements.txt"
echo "  3. Rode o bot:     python telegram_bot.py"
echo ""
