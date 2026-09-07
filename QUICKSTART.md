# 🚀 Guia de Início Rápido

## 1️⃣ Pré-requisitos

Antes de começar, certifique-se de ter:

- ✅ Python **3.12**
- ✅ `uv` (preferido) ou pip
- ✅ Conexão com a internet (dependências / tessdata)

## 2️⃣ Instalação Passo a Passo

### Windows / Linux / macOS

Na raiz do **KreuzbergParser** (Python 3.12):

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
./scripts/update_deps.sh --sync --cuda cu130
.venv/bin/python main.py
```

Kreuzberg embute Tesseract e baixa `por`/`eng` em `tessdata/` na primeira execução.
No Cursor, selecione o interpretador **`.venv`**, não `.venv-nemotron`.

**Nemotron Parse (opcional, venv isolado):** ver [`GPU_SETUP.md`](GPU_SETUP.md).

## 3️⃣ Primeiro Uso

### Interface Gráfica

Quando executar `.venv/bin/python main.py`, a interface gráfica abrirá automaticamente:

1. **Selecionar PDF**: Clique no botão "📂 Selecionar PDF"
2. **Escolher Destino**: Clique no botão "📁 Pasta Destino"
3. **Modo de Processamento**:
   - ⚡ **Express (Rápido) - Tesseract 200 DPI** — teste rápido
   - 💻 **CPU - Tesseract 300 DPI + tabelas** — documentos complexos sem GPU
   - 🚀 **GPU - EasyOCR CUDA** — se o Status GPU indicar NVIDIA
4. **VLM fallback**: Desligado, Qwen2.5-VL (Ollama) ou Nemotron Parse (vLLM)
5. **Processar**: Clique em "🚀 PROCESSAR PDF"
6. **Resultado**: `{stem}_ocr_{modo}_{modelo}.md` (ex. `_ocr_gpu_nemotron.md`)

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

GPU detectada: NVIDIA GeForce RTX 5060 Ti (16GB VRAM)
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

2. PyTorch com CUDA no `.venv`?
   ```bash
   .venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
   ./scripts/update_deps.sh --sync --cuda cu130
   ```

> O modo GPU usa EasyOCR + TrOCR via PyTorch. O CUDA Toolkit (`nvcc`) **não** é necessário
> para o KreuzbergParser. Só o servidor Nemotron Parse (vLLM/FlashInfer) precisa de
> `./scripts/install_cuda_toolkit.sh`. Ver [`GPU_SETUP.md`](GPU_SETUP.md).

### ❌ Erro: "CUDA out of memory" (GPU mode)

**Solução rápida:** reduza o DPI em `config.py` (GPU usa 300 em formulários) ou use CPU/Express. Se o Nemotron estiver no ar, baixe `NEMOTRON_GPU_MEM` (ex. 0.60).

### ❌ Interface não abre

**Solução:**
```bash
pip install --upgrade flet
python main.py
```

Se ainda não funcionar, verificar logs em `logs/` e se o Cursor está usando `.venv` (não `.venv-nemotron`).

### ❌ Log e barra de progresso só mudam ao clicar na janela / Alt+Tab

Isso é um bug clássico do Flet 0.86: `page.update()` feito fora do event loop da sessão não chega ao Flutter até o próximo evento da janela.

O KreuzbergParser já corrige isso (`asyncio.to_thread` + `utils/flet_ui.py`). Se ainda ocorrer após atualizar o código, não chame `page.update()` de threads próprias; no Wayland, como fallback: `GDK_BACKEND=x11 .venv/bin/python main.py`.

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
4. Modo: **Express (Rápido) - Tesseract 200 DPI**
5. Clicar "Processar"
6. Verificar `{stem}_ocr_express_nenhum.md` (ou o sufixo do VLM escolhido)

## 6️⃣ Próximos Passos

Agora que o sistema está funcionando:

1. ✅ **Testar com PDFs reais** do tribunal
2. ✅ **Experimentar diferentes modos** (Express, CPU, GPU)
3. ✅ **Revisar resultados Markdown** gerados
4. ✅ **Ajustar configurações** em `config.py` se necessário
5. ✅ **Ler README.md** para funcionalidades avançadas

## 📚 Documentação Completa

- **README.md** - Documentação completa do sistema
- **GPU_SETUP.md** - GPU cu130, Ollama, Nemotron
- **docs/DEPENDENCY_CONFLICTS.md** - o que não misturar
- **config.py** - Modos, VLM, templates

## 🆘 Suporte

Se encontrar problemas:

1. ✅ Executar `python test_setup.py` para diagnóstico
2. ✅ Verificar logs em `logs/` (`{CNJ}_ocr_{modo}_{modelo}_*.log` após processar)
3. ✅ Consultar [`GPU_SETUP.md`](GPU_SETUP.md) e [`docs/DEPENDENCY_CONFLICTS.md`](docs/DEPENDENCY_CONFLICTS.md)
4. ✅ Revisar documentação do Kreuzberg

---

**Boa sorte com o processamento de documentos judiciais!** ⚖️🇧🇷
