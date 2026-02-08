# 🚀 Guia de Início Rápido

## 1️⃣ Pré-requisitos

Antes de começar, certifique-se de ter:

- ✅ Python 3.9 ou superior instalado
- ✅ pip funcionando
- ✅ Conexão com a internet (para baixar dependências)

## 2️⃣ Instalação Passo a Passo

### Windows

```powershell
# 1. Navegar até o diretório do projeto
cd caminho\para\intelligent_ocr_system

# 2. Criar ambiente virtual
python -m venv venv

# 3. Ativar ambiente virtual
venv\Scripts\activate

# 4. Atualizar pip
python -m pip install --upgrade pip

# 5. Instalar dependências
pip install -r requirements.txt

# 6. Instalar Tesseract OCR
# Baixar e instalar de: https://github.com/UB-Mannheim/tesseract/wiki
# Adicionar ao PATH: C:\Program Files\Tesseract-OCR

# 7. Testar instalação
python test_setup.py

# 8. Executar aplicação
python main.py
```

### Linux (Ubuntu/Debian)

```bash
# 1. Navegar até o diretório do projeto
cd /caminho/para/intelligent_ocr_system

# 2. Criar ambiente virtual
python3 -m venv venv

# 3. Ativar ambiente virtual
source venv/bin/activate

# 4. Atualizar pip
pip install --upgrade pip

# 5. Instalar dependências
pip install -r requirements.txt

# 6. Instalar Tesseract OCR
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-por

# 7. Testar instalação
python test_setup.py

# 8. Executar aplicação
python main.py
```

### macOS

```bash
# 1. Navegar até o diretório do projeto
cd /caminho/para/intelligent_ocr_system

# 2. Criar ambiente virtual
python3 -m venv venv

# 3. Ativar ambiente virtual
source venv/bin/activate

# 4. Atualizar pip
pip install --upgrade pip

# 5. Instalar dependências
pip install -r requirements.txt

# 6. Instalar Tesseract OCR
brew install tesseract tesseract-lang

# 7. Testar instalação
python test_setup.py

# 8. Executar aplicação
python main.py
```

## 3️⃣ Primeiro Uso

### Interface Gráfica

Quando executar `python main.py`, a interface gráfica abrirá automaticamente:

1. **Selecionar PDF**: Clique no botão "📂 Selecionar PDF"
2. **Escolher Destino**: Clique no botão "📁 Pasta Destino"
3. **Modo de Processamento**: 
   - Comece com ⚡ **Express** para teste rápido
   - Use 💻 **CPU** para documentos complexos
   - Use 🚀 **GPU** somente se tiver NVIDIA GPU (verifique no Status GPU)
4. **Processar**: Clique em "🚀 PROCESSAR PDF"
5. **Resultado**: Arquivo `.md` será salvo na pasta escolhida

### Primeira Execução - O que esperar

**Console mostrará:**
```
======================================================================
Sistema Inteligente de OCR para PDFs Judiciais
Powered by Kreuzberg - https://kreuzberg.dev/
======================================================================

🚀 Code Reduction: 90% less custom code
⚡ Performance: 3-5x faster with Rust core
📦 Formats: 50+ file formats supported

GPU detectada: NVIDIA GeForce RTX 3080 (10GB VRAM)
[ou]
GPU não detectada - Modo GPU indisponível

🎯 Iniciando interface gráfica...
```

## 4️⃣ Solução de Problemas Comuns

### ❌ Erro: "ModuleNotFoundError: No module named 'kreuzberg'"

**Solução:**
```bash
pip install kreuzberg
```

### ❌ Erro: "Tesseract not found"

**Windows:**
1. Baixar: https://github.com/UB-Mannheim/tesseract/wiki
2. Instalar em: `C:\Program Files\Tesseract-OCR`
3. Adicionar ao PATH do Windows

**Linux:**
```bash
sudo apt install tesseract-ocr tesseract-ocr-por
```

**macOS:**
```bash
brew install tesseract tesseract-lang
```

### ❌ Erro: "CUDA not available" (GPU mode)

**Verificações:**
1. GPU NVIDIA presente?
   ```bash
   nvidia-smi
   ```

2. PyTorch com CUDA?
   ```bash
   pip uninstall torch torchvision torchaudio
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
   ```

> Nota: o modo GPU usa EasyOCR + TrOCR via PyTorch. O CUDA Toolkit (nvcc) nao e necessario
> para executar o projeto; basta o driver NVIDIA e o wheel CUDA correto do PyTorch.

### ❌ Erro: "CUDA out of memory" (GPU mode)

**Solução rápida:** o modo GPU usa DPI 200 e `force_ocr` desativado por padrão. Se ainda estourar VRAM,
reduza o DPI em `config.py` ou use CPU/Express.

### ❌ Interface não abre

**Solução:**
```bash
pip install --upgrade flet
python main.py
```

Se ainda não funcionar, verificar logs em `logs/ocr_*.log`

## 5️⃣ Teste Rápido com PDF

### Criar PDF de Teste (opcional)

Se não tiver um PDF para testar, pode criar um simples:

**No Python:**
```python
from reportlab.pdfgen import canvas

c = canvas.Canvas("teste.pdf")
c.drawString(100, 750, "Este é um PDF de teste")
c.drawString(100, 700, "para o Sistema de OCR")
c.showPage()
c.save()
```

**Ou online:**
- https://www.sejda.com/html-to-pdf (converter texto para PDF)

### Processar Teste

1. Executar: `python main.py`
2. Selecionar seu PDF de teste
3. Escolher pasta destino
4. Modo: **Express** (mais rápido para teste)
5. Clicar "Processar"
6. Verificar arquivo `.md` gerado

## 6️⃣ Próximos Passos

Agora que o sistema está funcionando:

1. ✅ **Testar com PDFs reais** do tribunal
2. ✅ **Experimentar diferentes modos** (Express, CPU, GPU)
3. ✅ **Revisar resultados Markdown** gerados
4. ✅ **Ajustar configurações** em `config.py` se necessário
5. ✅ **Ler README.md** para funcionalidades avançadas

## 📚 Documentação Completa

- **README.md** - Documentação completa do sistema
- **[Kreuzberg Docs](https://docs.kreuzberg.dev/)** - Documentação da biblioteca OCR
- **config.py** - Todas as configurações disponíveis

## 🆘 Suporte

Se encontrar problemas:

1. ✅ Executar `python test_setup.py` para diagnóstico
2. ✅ Verificar logs em `logs/ocr_*.log`
3. ✅ Consultar seção Troubleshooting no README.md
4. ✅ Revisar documentação do Kreuzberg

---

**Boa sorte com o processamento de documentos judiciais!** ⚖️🇧🇷
