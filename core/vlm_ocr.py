"""OpenAI-compatible VLM client for document-page fallback OCR.

Supports:
- NVIDIA Nemotron Parse (vLLM local) — task-specific document parser
- Qwen2.5-VL / Qwen3-VL (Ollama OpenAI endpoint) — free-form prompt
- PaddleOCR-VL (vLLM local) — task-specific document parser
- NVIDIA NIM API (optional, sends documents off-machine)

Degrades gracefully: returns None when the backend is down or times out.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Endpoints that refused a connection, so a whole run does not pay one failed
# connect (and one warning) per page. Cleared by reset_vlm_health().
_unreachable: Dict[str, str] = {}

LLAMAPARSE_SYSTEM_PROMPT = """\
Você é um extrator de documentos judiciais brasileiros. Transcreva a página \
com fidelidade absoluta.

Regras:
- Preserve todos os números, datas, CNPJ, CPF, PIS e valores monetários exatamente.
- Formulários (TRCT, ficha de registro, recibo, FGTS, CNH): use \
`**rótulo**: valor` (um campo por linha). Campos numerados do TRCT no formato \
`**01 CNPJ/CEI**: ...`.
- Tabelas: HTML com <table>, <thead>, <tbody>, <th>, <td> e colspan/rowspan \
quando houver células mescladas.
- Títulos: `#` para o nome do documento, `##` para seções.
- Descreva logos, assinaturas, carimbos e QR codes entre colchetes, \
ex.: `[signature: NOME]`, `[QR Code]`.
- Não invente dados. Se um campo estiver vazio, deixe o valor em branco.
- Responda somente com o markdown/HTML da página, sem prefácio.
"""

IMAGE_DESCRIBE_SYSTEM_PROMPT = """\
Você descreve fotografias e imagens embutidas em processos judiciais \
brasileiros. Seja factual e objetivo.

Regras:
- Descreva o que a imagem mostra: ambiente, objetos, estado de conservação, \
condições de higiene, pessoas (sem inventar nomes ou identidades), ações.
- Se houver texto legível na imagem (legendas, placas, rótulos), transcreva-o.
- Não invente detalhes que não estejam visíveis.
- Responda em português brasileiro, em markdown curto (1–3 parágrafos), \
sem prefácio nem conclusões jurídicas.
"""

IMAGE_DESCRIPTION_HEADING = "### Descrição da imagem"

# Official Nemotron Parse 2.0 task prompt (HF model card).
NEMOTRON_PARSE_PROMPT = (
    "</s><s><predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>"
)

# PaddleOCR-VL task prompt (vLLM recipe). Others: "Table Recognition:",
# "Formula Recognition:", "Chart Recognition:", "Seal Recognition:".
PADDLEOCR_VL_PROMPT = "OCR:"

# One 300 DPI page is ~4300 prompt tokens; Ollama's default context is 4096.
OLLAMA_NUM_CTX = 16384
# Caption replies are short; keep generation budget modest.
OLLAMA_DESCRIBE_NUM_PREDICT = 1024


def parse_page_image(
    png_bytes: bytes,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout_s: Optional[float] = None,
) -> Optional[str]:
    """Send a page raster to the configured VLM. Return markdown or None."""
    try:
        from config import Config
    except Exception:
        Config = None  # type: ignore

    base_url = (base_url or _cfg(Config, "VLM_BASE_URL", "http://127.0.0.1:11434/v1")).rstrip("/")
    model = model or _cfg(Config, "VLM_MODEL", "qwen3-vl:8b-instruct")
    api_key = api_key if api_key is not None else _cfg(Config, "VLM_API_KEY", "")
    if not api_key:
        api_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("VLM_API_KEY") or "ollama"
    timeout_s = float(timeout_s if timeout_s is not None else _cfg(Config, "VLM_TIMEOUT_S", 90))

    if not png_bytes:
        return None

    if base_url in _unreachable:
        logger.debug(
            "VLM ignorado: %s inacessível nesta execução (%s)",
            base_url,
            _unreachable[base_url],
        )
        return None

    parse_mode = _is_nemotron_parse(model)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    url = f"{base_url}/chat/completions"

    if parse_mode:
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 9000,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": NEMOTRON_PARSE_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
    elif _is_paddleocr_vl(model):
        # Task prompt only, image first; the model ignores free-form instructions.
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": PADDLEOCR_VL_PROMPT},
                    ],
                },
            ],
        }
    elif _is_ollama(base_url):
        # Ollama's OpenAI endpoint cannot set the context size and rejects the
        # page with HTTP 400; the native API takes num_ctx per request.
        url = f"{base_url[: -len('/v1')]}/api/chat"
        payload = {
            "model": model,
            "stream": False,
            # Thinking models can spend the whole num_predict budget reasoning
            # and return empty content. qwen3-vl:8b ignores this flag, which is
            # why the preset uses the -instruct tag.
            "think": False,
            "options": {
                "temperature": 0,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": 4096,
            },
            "messages": [
                {"role": "system", "content": LLAMAPARSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Transcreva esta página.",
                    "images": [b64],
                },
            ],
        }
    else:
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": LLAMAPARSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Transcreva esta página."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        logger.warning("VLM HTTP %s (%s): %s", exc.code, url, detail)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _mark_unreachable(base_url, model, exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("VLM returned non-JSON payload")
        return None

    text = _message_text(data)
    if not text:
        return None
    if parse_mode:
        text = postprocess_nemotron_parse(text)
    return text.strip() or None


def supports_image_description(
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> bool:
    """True when the configured VLM can free-form caption photos (Qwen/Ollama)."""
    try:
        from config import Config
    except Exception:
        Config = None  # type: ignore
    base_url = (base_url or _cfg(Config, "VLM_BASE_URL", "http://127.0.0.1:11434/v1")).rstrip("/")
    model = model or _cfg(Config, "VLM_MODEL", "qwen3-vl:8b-instruct")
    if _is_nemotron_parse(model) or _is_paddleocr_vl(model):
        return False
    # Free-form caption: Ollama Qwen path, or any OpenAI-compatible non-parse model.
    return bool(model) and (_is_ollama(base_url) or "qwen" in (model or "").lower())


def format_image_description_block(
    description: str,
    *,
    index: Optional[int] = None,
) -> str:
    """Markdown block inserted into page text for a described figure/page.

    A single caption keeps ``### Descrição da imagem``. When several
    captions share a page, pass ``index`` (1-based) so the headings differ.
    """
    body = (description or "").strip()
    if not body:
        return ""
    if index is None or int(index) < 1:
        if body.startswith(IMAGE_DESCRIPTION_HEADING):
            return body
        return f"{IMAGE_DESCRIPTION_HEADING}\n{body}"
    heading = f"{IMAGE_DESCRIPTION_HEADING} {int(index)}"
    if (
        body == heading
        or body.startswith(heading + "\n")
        or body.startswith(heading + "\r\n")
    ):
        return body
    return f"{heading}\n{body}"


def describe_image(
    png_bytes: bytes,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout_s: Optional[float] = None,
) -> Optional[str]:
    """Caption a photograph / figure with no usable OCR text. Qwen/Ollama only."""
    try:
        from config import Config
    except Exception:
        Config = None  # type: ignore

    base_url = (base_url or _cfg(Config, "VLM_BASE_URL", "http://127.0.0.1:11434/v1")).rstrip("/")
    model = model or _cfg(Config, "VLM_MODEL", "qwen3-vl:8b-instruct")
    api_key = api_key if api_key is not None else _cfg(Config, "VLM_API_KEY", "")
    if not api_key:
        api_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("VLM_API_KEY") or "ollama"
    timeout_s = float(timeout_s if timeout_s is not None else _cfg(Config, "VLM_TIMEOUT_S", 90))

    if not png_bytes:
        return None
    if not supports_image_description(base_url=base_url, model=model):
        logger.debug(
            "describe_image ignorado: modelo %s não suporta caption livre",
            model,
        )
        return None

    if base_url in _unreachable:
        logger.debug(
            "VLM ignorado: %s inacessível nesta execução (%s)",
            base_url,
            _unreachable[base_url],
        )
        return None

    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    url = f"{base_url}/chat/completions"

    if _is_ollama(base_url):
        url = f"{base_url[: -len('/v1')]}/api/chat"
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": OLLAMA_DESCRIBE_NUM_PREDICT,
            },
            "messages": [
                {"role": "system", "content": IMAGE_DESCRIBE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Descreva esta imagem.",
                    "images": [b64],
                },
            ],
        }
    else:
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": OLLAMA_DESCRIBE_NUM_PREDICT,
            "messages": [
                {"role": "system", "content": IMAGE_DESCRIBE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Descreva esta imagem."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        logger.warning("VLM describe HTTP %s (%s): %s", exc.code, url, detail)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _mark_unreachable(base_url, model, exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("VLM describe returned non-JSON payload")
        return None

    text = _message_text(data)
    return (text or "").strip() or None


def vlm_available(
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout_s: float = 8.0,
) -> bool:
    """Cheap health check against the OpenAI-compatible `/models` endpoint.

    Default timeout is generous: cold Ollama startups often exceed 2s.
    """
    try:
        from config import Config
    except Exception:
        Config = None  # type: ignore
    base_url = (base_url or _cfg(Config, "VLM_BASE_URL", "http://127.0.0.1:11434/v1")).rstrip("/")
    url = f"{base_url}/models"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            ok = 200 <= response.status < 300
    except Exception as exc:
        _mark_unreachable(base_url, model, exc)
        return False
    if ok:
        _unreachable.pop(base_url, None)
    return ok


def reset_vlm_health() -> None:
    """Forget cached unreachable endpoints (call once per processing run)."""
    _unreachable.clear()


def _mark_unreachable(base_url: str, model: Optional[str], exc: BaseException) -> None:
    """Log the outage once per endpoint and skip further attempts."""
    if base_url in _unreachable:
        return
    _unreachable[base_url] = str(exc)
    logger.warning(
        "VLM indisponível em %s (%s). Nesta execução ficam desativados o "
        "fallback de formulários e a descrição de fotografias; a extração "
        "segue por template + OCR. Para habilitar: inicie o Ollama "
        "(`ollama serve`) e garanta o modelo (`ollama pull %s`), ou "
        "desmarque o VLM na interface.",
        base_url,
        exc,
        model or "qwen3-vl:8b-instruct",
    )


def postprocess_nemotron_parse(text: str) -> str:
    """Strip Nemotron Parse class/bbox control tokens, keep readable markdown."""
    out = text or ""
    out = re.sub(r"</?class_[A-Za-z0-9_]+>", "", out)
    out = re.sub(r"</?predict_[A-Za-z0-9_]+>", "", out)
    out = re.sub(r"<x_\d+>", "", out)
    out = re.sub(r"<y_\d+>", "", out)
    out = re.sub(r"</?bbox>", "", out)
    out = re.sub(r"\[(\d+(?:[.,]\d+)?),\s*(\d+(?:[.,]\d+)?),\s*(\d+(?:[.,]\d+)?),\s*(\d+(?:[.,]\d+)?)\]", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _is_nemotron_parse(model: str) -> bool:
    low = (model or "").lower()
    return "nemotron-parse" in low or "nemotron_parse" in low or "nemoretriever-parse" in low


def _is_ollama(base_url: str) -> bool:
    return ":11434" in base_url and base_url.endswith("/v1")


def _is_paddleocr_vl(model: str) -> bool:
    low = (model or "").lower()
    return "paddleocr-vl" in low or "paddleocr_vl" in low


def _message_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices and not data.get("message"):
        return (data.get("text") or data.get("output") or "") if isinstance(data, dict) else ""
    # Ollama's native /api/chat returns the message at the top level.
    message = choices[0].get("message") or {} if choices else data["message"]
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                parts.append(item.get("text") or "")
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _cfg(config_cls, name: str, default):
    if config_cls is None:
        return default
    return getattr(config_cls, name, default)
