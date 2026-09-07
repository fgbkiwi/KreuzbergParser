# 🎯 Sistema Inteligente de OCR para PDFs Judiciais
## Powered by Kreuzberg 🚀

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Kreuzberg](https://img.shields.io/badge/Kreuzberg-0.6.0-green.svg)](https://kreuzberg.dev/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Sistema profissional de OCR otimizado para processos judiciais trabalhistas brasileiros**, com interface gráfica moderna e processamento inteligente de documentos mistos.

---

## ✨ Destaques da Implementação

### 🎯 **90% Menos Código**
- **Original**: 1,500+ linhas de código customizado
- **Com Kreuzberg**: ~150 linhas de código core
- **Resultado**: Manutenção drasticamente simplificada

### ⚡ **3-5x Mais Rápido**
- Core em Rust com otimizações SIMD
- Processamento paralelo nativo
- Streaming de arquivos grandes

### 📦 **50+ Formatos Suportados**
- PDFs (nativos e escaneados)
- Documentos Office (DOCX, XLSX, PPTX)
- Imagens (PNG, JPG, TIFF, etc.)
- E-mails, arquivos compactados, e mais

---

## 📋 Funcionalidades

### 🔍 Detecção Automática Inteligente
- ✅ Identifica páginas nativas vs escaneadas
- ✅ Detecta tabelas automaticamente
- ✅ Reconhece idioma do texto
- ✅ Aplica OCR apenas quando necessário

### 🚀 Três Modos de Processamento

#### ⚡ **Express (Rápido) - Tesseract 200 DPI**
- Tesseract OCR
- Processamento mínimo
- Ideal para documentos simples
- **~0.5-1 página/segundo**

#### 💻 **CPU - Tesseract 300 DPI + tabelas**
- Tesseract OCR com pré-processamento
- Correção de perspectiva e rotação
- Detecção de tabelas
- **~0.2-0.5 página/segundo**

#### 🚀 **GPU - EasyOCR CUDA**
- EasyOCR GPU-accelerated
- TrOCR para texto manuscrito (opcional)
- Máxima acurácia
- **~1-2 páginas/segundo**
- **Requisitos**: NVIDIA GPU (RTX 50 / CUDA 13 no alvo atual)
- DPI de formulários: 300 (`config.py`)

### 📋 Formulários trabalhistas + VLM local
- Templates determinísticos (Tesseract TSV + geometria) para TRCT, ficha de registro, recibo e FGTS
- Fallback VLM só em páginas de formulário/tabela, via endpoint OpenAI-compatível
- Na UI: **Desligado**, **Qwen2.5-VL (Ollama)** ou **Nemotron Parse (vLLM)**
- Nemotron corre em **`.venv-nemotron`** (não misturar com o `.venv` do OCR) — ver [`GPU_SETUP.md`](GPU_SETUP.md)

### 📝 Exportação Markdown
- Documento estruturado
- Metadados e estatísticas
- Nome do arquivo inclui modo + modelo VLM, por exemplo:
  - `Processo_…_ocr_gpu_nemotron.md`
  - `Processo_…_ocr_gpu_qwen.md`
  - `Processo_…_ocr_gpu_nenhum.md`
- Logs de conversão e auditoria usam o mesmo sufixo; o log genérico da sessão ganha prefixo CNJ e sufixo ao clicar **PROCESSAR PDF**

---

## 🛠️ Instalação

### Requisitos do Sistema

**Mínimos (Express/CPU):**
- Python **3.12** (wheels CUDA; evitar ≥3.13)
- 4GB RAM

**Recomendados (GPU / VLM local):**
- Python 3.12
- 16GB RAM
- NVIDIA GPU (alvo atual: RTX 50-series, driver 580+, PyTorch **cu130**)
- Driver NVIDIA atualizado; o KreuzbergParser **não** precisa de `nvcc`
- `nvcc` (CUDA Toolkit 13.0) só para o servidor Nemotron Parse / FlashInfer

### Passo 1: Clonar/Baixar Projeto

```bash
cd /caminho/para/KreuzbergParser
```

### Passo 2: Criar Ambiente Virtual

Recomendamos **Python 3.12** e **[uv](https://docs.astral.sh/uv/)** para instalação reproduzível.

#### Windows (recomendado — uv)

```powershell
# Criar ambiente (.venv na raiz do projeto)
uv venv .venv --python 3.12

# Ativar
.\.venv\Scripts\Activate.ps1
```

#### Linux / macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### Passo 3: Instalar Dependências

O fluxo depende do sistema operacional. Veja a seção
[Dependências no Windows (uv)](#-dependências-no-windows-uv) para o motivo dessa diferença.

#### Windows (uv) — preferido

```powershell
.\scripts\update_deps.ps1 -Sync
```

Isso cria/usa `.venv` (Python 3.12), instala `torch`/`torchvision` do índice CUDA
(`cu130` por padrão) e o restante a partir de `requirements.in`, com checagem de
conflitos. Equivalente manual:

```powershell
uv pip install torch torchvision `
  --python .venv\Scripts\python.exe `
  --index-url https://download.pytorch.org/whl/cu130
uv pip install -r requirements.in --python .venv\Scripts\python.exe
```

#### Linux / macOS

```bash
# Preferido: resolve lockfile + sync
./scripts/update_deps.sh --sync --cuda cu130

# Ou sync direto do lockfile gerado
uv pip sync requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cu130 \
  --index-strategy unsafe-best-match

# PaddleOCR GPU nativo: wheel Kreuzberg com ort-dynamic
./scripts/build_kreuzberg_gpu.sh
```

> Regras para não misturar torch CPU, Paddle e vLLM no mesmo venv:
> [`docs/DEPENDENCY_CONFLICTS.md`](docs/DEPENDENCY_CONFLICTS.md) e [`GPU_SETUP.md`](GPU_SETUP.md).

> No **Windows**, `pip install -r requirements.txt` e `uv pip sync requirements.txt`
> falham porque o lockfile inclui pacotes NVIDIA (`nvidia-cufile`, etc.) compilados
> apenas para Linux. Use `.\scripts\update_deps.ps1 -Sync`.

### Passo 4: Instalar Tesseract OCR

#### **Windows:**
1. Baixar instalador: https://github.com/UB-Mannheim/tesseract/wiki
2. Instalar em `C:\Program Files\Tesseract-OCR`
3. Adicionar ao PATH do sistema

#### **Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-por
```

#### **macOS:**
```bash
brew install tesseract tesseract-lang
```

### Passo 5: Verificar GPU (Opcional - para GPU Mode)

```bash
# Verificar CUDA disponível
python -c "import torch; print('CUDA available:', torch.cuda.is_available())"

# Se CUDA não disponível, instalar PyTorch com CUDA (RTX 50: cu130):
# pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
```

---

## 🧩 GPU Setup (EasyOCR + TrOCR + PaddleOCR GPU)

Guia atual (RTX 50 / cu130, PaddleOCR nativo, Nemotron, Ollama): **[`GPU_SETUP.md`](GPU_SETUP.md)**.

O modo **EasyOCR GPU** usa PyTorch no `.venv`. O modo **PaddleOCR GPU** usa o Kreuzberg nativo (`AccelerationConfig(provider="cuda")`) com o wheel local `ort-dynamic` e `onnxruntime-gpu` — sem fallback para o pacote Python `paddleocr`. Você precisa de:
- Driver NVIDIA atualizado
- PyTorch `+cu130` (Blackwell / `sm_120`)
- `onnxruntime-gpu` e o wheel em `vendor/wheels/`

```bash
./scripts/update_deps.sh --sync --cuda cu130
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"
```

No Cursor, o interpretador do `main.py` deve ser **`.venv/bin/python`**, não `.venv-nemotron` nem `/bin/python3`.

**Nemotron Parse (opcional):** venv isolado + `nvcc` 13.0 — `./scripts/install_cuda_toolkit.sh` e `./scripts/start_nemotron_parse.sh`.

---

## 🪟 Dependências no Windows (uv)

O arquivo `requirements.txt` é **gerado automaticamente** no Linux pelo script
`scripts/update_deps.sh`. Ele fixa versões exatas de todo o grafo de dependências (incluindo
PyTorch CUDA e bibliotecas NVIDIA usadas apenas em wheels Linux). Por isso, o ambiente Windows
segue um caminho ligeiramente diferente.

### Por que não usar `requirements.txt` diretamente no Windows?

Ao rodar:

```powershell
uv pip sync requirements.txt --extra-index-url https://download.pytorch.org/whl/cu130
```

o resolver tenta instalar pacotes como `nvidia-cufile`, que **não possuem wheel para
`win_amd64`**. A instalação falha mesmo que o restante do stack seja compatível.

**Solução:** use `.\scripts\update_deps.ps1 -Sync` (ou o fluxo manual em duas etapas:
PyTorch no índice CUDA + `requirements.in`), sem sincronizar o lockfile Linux.

### Grafo típico no Windows

Após a instalação em duas etapas, parte das bibliotecas entra como dependência transitiva do
PyTorch:

```
torch 2.13.0+cu130
├── filelock
├── fsspec
├── setuptools
├── typing-extensions
└── sympy → mpmath

transformers
└── tokenizers  (intervalo declarado: >=0.22.0, <=0.23.0)
```

Pacotes como `numpy` e `pillow` também ficam na versão resolvida naquele momento, embora
`easyocr`, `transformers` e outros consumidores aceitem faixas mais amplas.

### `uv pip list --outdated` — versões “desatualizadas”

É normal ver pacotes marcados como outdated mesmo com o ambiente saudável. O comando compara o
que está **instalado** com a **última versão no PyPI**, não com o que o projeto testou.

| Pacote | Por que pode aparecer desatualizado |
|--------|-------------------------------------|
| `filelock`, `fsspec`, `setuptools`, `typing-extensions`, `mpmath` | Instalados na versão resolvida junto com `torch`; o PyTorch declara requisitos **sem teto** (`filelock`, `fsspec`, …) |
| `numpy`, `pillow` | Versão fixada na resolução inicial; não há conflito explícito com versões mais novas na maioria dos casos |
| `tokenizers` | Limitado pelo `transformers` atual (`<=0.23.0`); a “Latest” no PyPI (`0.23.1`) pode estar **fora** do intervalo suportado |

Isso **não indica**, por si só, incompatibilidade — na maioria dos casos são **pins de
instalação** para reproducibilidade, não bloqueios rígidos.

### Boas práticas no Windows

- **Não atualize pacotes soltos** do stack CUDA (`torch`, `numpy`, etc.) sem re-resolver tudo.
- Para atualizar com segurança: `.\scripts\update_deps.ps1 -Sync`
- Só checar conflitos: `.\scripts\update_deps.ps1 -Check`
- Para regenerar o lockfile completo (incluindo pins Linux), use `./scripts/update_deps.sh` em
  Linux ou WSL.
- Nesta máquina (RTX 5060 Ti / Blackwell) use o índice **`cu130`**. Tags mais antigas
  não incluem `sm_120`.

---

## 📦 Instalador Windows (Pynsist)

Para usuários finais: baixe o `.exe` em
[Releases](https://github.com/fgbkiwi/KreuzbergParser/releases/latest).

Para mantenedores (NSIS + `gh` autenticado se for publicar):

```powershell
.\scripts\update_deps.ps1 -Sync
.\build_kreuzberg_parser_pynsist.ps1 -NoPublish   # gera build\nsis\KreuzbergParser_x.y.z.exe
.\build_kreuzberg_parser_pynsist.ps1              # bump patch + build + Release
.\build_kreuzberg_parser_pynsist.ps1 minor        # bump minor + Release
```

Fonte única da versão: `APP_VERSION` em `main.py` (inicia em **1.0.0**).
`bump_version.py` sincroniza `kreuzberg_parser_pynsist.cfg` e `pyproject.toml`.

---

## 🚀 Uso

### Iniciar Aplicação

```bash
# Linux / macOS
.venv/bin/python main.py

# Windows
.\.venv\Scripts\python.exe main.py
```

A interface gráfica será aberta automaticamente.

### Fluxo de Trabalho

1. **Selecionar PDF**: Clique em "📂 Selecionar PDF" e escolha o arquivo
2. **Pasta Destino**: Clique em "📁 Pasta Destino" para escolher onde salvar
3. **Escolher Modo**:
   - ⚡ Express (Rápido) - Tesseract 200 DPI
   - 💻 CPU - Tesseract 300 DPI + tabelas
   - 🚀 GPU - EasyOCR CUDA
4. **VLM fallback** (formulários / tabelas): Desligado, Qwen2.5-VL (Ollama) ou Nemotron Parse (vLLM)
5. **Opções**:
   - ✅ Gerar Markdown (recomendado)
   - 🖋️ Detectar manuscrito (apenas GPU)
6. **Processar**: Clique em "🚀 PROCESSAR PDF"
7. **Resultado**: `{stem}_ocr_{modo}_{modelo}.md` na pasta destino
   (ex.: `_ocr_gpu_nemotron.md`, `_ocr_cpu_nenhum.md`)

---

## 📊 Arquitetura

### Estrutura do Projeto

```
KreuzbergParser/
│
├── main.py                      # Entry point (+ APP_VERSION)
├── config.py                    # Configuração (modos, VLM, templates)
├── _kreuzberg_launcher.py       # Entry point do instalador Pynsist
├── kreuzberg_parser_pynsist.cfg # Config Pynsist
├── bump_version.py              # Bump major.minor.patch
├── build_kreuzberg_parser_pynsist.ps1
├── requirements.in              # Dependências diretas (editar aqui)
├── requirements.txt             # Lockfile com pins (Linux; gerado por update_deps.sh)
├── pyproject.toml               # Metadados / versão
│
├── core/
│   ├── kreuzberg_engine.py     # Engine OCR + roteamento template/VLM
│   ├── page_classifier.py      # nativa / híbrida / image_page
│   ├── pje_sumario.py          # SUMÁRIO PJe
│   ├── form_layout.py          # TSV Tesseract + grade
│   ├── form_templates.py       # TRCT / ficha / recibo / FGTS
│   ├── labor_forms.py          # Formatadores leves por tipo
│   ├── vlm_ocr.py              # Cliente OpenAI-compatível (Qwen/Nemotron)
│   ├── handwriting_detector.py # TrOCR (opcional)
│   └── markdown_converter.py   # Conversão para Markdown
│
├── ui/app.py                    # Interface Flet (updates no loop da sessão)
├── utils/                       # GPU, logging, tessdata, páginas PDF, flet_ui
├── scripts/
│   ├── update_deps.sh           # Linux: resolve + sync lockfile
│   ├── update_deps.ps1          # Windows: torch CUDA + requirements.in
│   ├── setup_nemotron_venv.sh
│   ├── start_nemotron_parse.sh
│   ├── install_cuda_toolkit.sh
│   └── compare_extraction.py
│
├── GPU_SETUP.md
├── docs/DEPENDENCY_CONFLICTS.md
├── logs/                        # ocr_*.log (renomeado com CNJ + sufixo)
├── tessdata/                    # por/eng (baixado na 1ª execução)
└── output/
```

### Fluxo de Processamento

```
PDF → Kreuzberg Engine → Detecção Automática
                       ↓
        ┌──────────────┴──────────────┐
        │                             │
    Nativo                       Escaneado
        │                             │
    Extração                      OCR + templates
    Direta                      (Tesseract/EasyOCR)
        │                             │
        └──────────────┬──────────────┘
                       ↓
            (Opcional) VLM fallback / TrOCR
                       ↓
               Markdown Export
```

---

## 🎯 Comparação: Original vs Kreuzberg

| Aspecto | Implementação Original | Com Kreuzberg | Benefício |
|---------|----------------------|---------------|-----------|
| **Linhas de Código** | ~1,500 | ~150 | **90% menos** |
| **Dependências** | 10+ bibliotecas | Kreuzberg + poucas | **Mais simples** |
| **Performance** | Python puro | Rust core | **3-5x mais rápido** |
| **Formatos** | PDF apenas | 50+ formatos | **50x mais** |
| **Manutenção** | Alta complexidade | Baixa | **95% menos trabalho** |
| **Estabilidade** | Custom code | Battle-tested | **Produção-pronto** |

---

## ⚙️ Configuração Avançada

### Ajustar Modos de Processamento

Edite `config.py`:

```python
MODE_CONFIGS = {
    ProcessingMode.EXPRESS: {
        "backend": "tesseract",
        "dpi": 200,
        "detect_tables": True,
        "skip_preprocessing": True
    },
    # ... outros modos
}
```

### Configurar Handwriting Detection

```python
# config.py
HANDWRITING_CONFIDENCE_THRESHOLD = 0.30  # Ajustar threshold
TROCR_MODEL = 'microsoft/trocr-base-handwritten'  # Modelo TrOCR
```

---

## 🐛 Troubleshooting

### Problema: Kreuzberg não encontrado

**Solução:**
```bash
pip install kreuzberg
```

### Problema: Tesseract não encontrado

**Solução:**
- Verificar instalação: `tesseract --version`
- Windows: Adicionar ao PATH
- Linux: `sudo apt install tesseract-ocr tesseract-ocr-por`

### Problema: GPU não detectada

**Verificações:**
1. GPU NVIDIA presente: `nvidia-smi`
2. PyTorch CUDA no **`.venv`**: `.venv/bin/python -c "import torch; print(torch.cuda.is_available())"`
3. Wheel `+cu130` (Blackwell / RTX 5060 Ti): `./scripts/update_deps.sh --sync --cuda cu130`

`nvcc` **não** é exigido pelo OCR. Só o servidor Nemotron Parse precisa do CUDA Toolkit 13.0.

### Problema: Flet não abre interface

**Solução:**
1. Verificar Python 3.9+: `python --version`
2. Reinstalar Flet: `pip install --upgrade flet`
3. Verificar logs em `logs/` (`{CNJ}_ocr_{modo}_{modelo}_AAAAMMDD_HHMMSS.log` após PROCESSAR PDF)

### Problema: log e barra de progresso só atualizam ao ganhar/perder foco

No Flet 0.86 desktop, `page.update()` de uma thread de trabalho enfileira o patch numa `asyncio.Queue` **não thread-safe**. O cliente Flutter só pinta quando um evento da janela (foco, clique) acorda o loop de envio.

**Correção neste projeto:** o OCR corre em `asyncio.to_thread`; log, barra e estatísticas só chamam `page.update()` no event loop da sessão (`page.session.connection.loop`), via `utils/flet_ui.py`. Não chame `page.update()` de `threading.Thread` / `threading.Timer`.

Se, no Pop!_OS/Wayland, a janela ainda atrasar frames depois disso, teste `GDK_BACKEND=x11 .venv/bin/python main.py` (XWayland).

---

## 📚 Documentação Adicional

- **[QUICKSTART.md](QUICKSTART.md)** - Instalação e primeiro uso
- **[GPU_SETUP.md](GPU_SETUP.md)** - RTX 50 / cu130, Ollama, Nemotron Parse
- **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** - Arquivos, fluxo e UI Flet (`utils/flet_ui.py`)
- **[docs/DEPENDENCY_CONFLICTS.md](docs/DEPENDENCY_CONFLICTS.md)** - o que não misturar nos venvs
- **[Kreuzberg Docs](https://docs.kreuzberg.dev/)** - Documentação oficial
- **[Flet Docs](https://flet.dev/)** - Framework UI

---

## 🎓 Casos de Uso - Tribunais Trabalhistas

### ✅ Documentos Suportados

- **Petições iniciais** (PDF nativo) → Extração direta em ~1s
- **Contracheques** (PDF escaneado) → OCR + detecção de tabelas
- **Termos de rescisão** (PDF escaneado) → OCR estruturado
- **Folhas de ponto** (foto de celular) → Correção perspectiva + OCR
- **Anotações manuscritas** (foto) → TrOCR para manuscrito

### 📊 Performance Esperada

**Processo típico (60 páginas):**
- 20 páginas nativas → ~5s (extração direta)
- 30 páginas escaneadas → ~60-120s (OCR)
- 10 páginas com manuscrito → ~30-60s (TrOCR GPU)
- **Total: ~95-185s (1.5-3 minutos)**

---

## 🤝 Contribuições

Este é um projeto de proof-of-concept demonstrando integração com Kreuzberg.

Para sugestões ou melhorias:
1. Documente o problema/melhoria
2. Teste sua solução
3. Compartilhe feedback

---

## 📜 Licença

MIT License - uso livre em sistemas judiciais e comerciais.

---

## 🙏 Agradecimentos

- **[Kreuzberg](https://kreuzberg.dev/)** - Por fornecer uma biblioteca OCR extraordinária
- **[Flet](https://flet.dev/)** - Framework UI moderno em Python
- **Comunidade Open Source** - Por todas as bibliotecas utilizadas

---

## 📞 Suporte

Para questões técnicas:
1. Verificar troubleshooting acima
2. Consultar documentação do Kreuzberg
3. Revisar logs em `logs/`

---

**Desenvolvido para Juízes e Servidores dos Tribunais Regionais do Trabalho do Brasil** ⚖️🇧🇷
