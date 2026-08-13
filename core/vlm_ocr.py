"""OpenAI-compatible VLM client for document-page fallback OCR.

Supports:
- NVIDIA Nemotron Parse (vLLM local) — task-specific document parser
- Qwen2.5-VL / Qwen3-VL (Ollama OpenAI endpoint) — free-form prompt
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
from typing import Optional

logger = logging.getLogger(__name__)

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

# Official Nemotron Parse 2.0 task prompt (HF model card).
NEMOTRON_PARSE_PROMPT = (
    "</s><s><predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>"
)


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
    model = model or _cfg(Config, "VLM_MODEL", "qwen2.5vl:7b")
    api_key = api_key if api_key is not None else _cfg(Config, "VLM_API_KEY", "")
    if not api_key:
        api_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("VLM_API_KEY") or "ollama"
    timeout_s = float(timeout_s if timeout_s is not None else _cfg(Config, "VLM_TIMEOUT_S", 90))

    if not png_bytes:
        return None

    parse_mode = _is_nemotron_parse(model)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"

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

    url = f"{base_url}/chat/completions"
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
        logger.warning("VLM unavailable at %s: %s", url, exc)
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


def vlm_available(
    *,
    base_url: Optional[str] = None,
    timeout_s: float = 2.0,
) -> bool:
    """Cheap health check against the OpenAI-compatible `/models` endpoint."""
    try:
        from config import Config
    except Exception:
        Config = None  # type: ignore
    base_url = (base_url or _cfg(Config, "VLM_BASE_URL", "http://127.0.0.1:11434/v1")).rstrip("/")
    url = f"{base_url}/models"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


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


def _message_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return (data.get("text") or data.get("output") or "") if isinstance(data, dict) else ""
    message = choices[0].get("message") or {}
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
