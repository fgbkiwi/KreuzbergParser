"""
Contagem de tokens do Markdown convertido e comparação com o log do KiwiSplitter.

OpenAI usa tiktoken (cl100k). Gemini usa o SentencePiece Gemma3 já em cache
pelo KiwiSplitter, com a mesma margem de 6% do AI Studio. Se o tokenizer
não estiver disponível, entra a mesma heurística de português jurídico.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONTEXT_TOKEN_LIMIT = 800_000
DIVERGENCE_RATIO = 0.20
AI_STUDIO_UPLOAD_MARGIN = 1.06

_OPENAI = "openai"
_GEMINI = "gemini"

_KIWI_ESTIMATE_RE = re.compile(
    r"Total estimado \(documentos selecionados / arquivo sanitizado\):\s*"
    r"openai=(\d+)\s+gemini=(\d+)",
    re.IGNORECASE,
)

_encoder = None
_encoder_failed = False
_sentencepiece = None
_sentencepiece_failed = False
_tokenizer_lock = threading.Lock()


@dataclass(frozen=True)
class ProviderTokens:
    key: str
    label: str
    tokens: int
    method: str

    def formatted(self) -> str:
        return f"{self.tokens:,} ({self.method})"


@dataclass(frozen=True)
class MarkdownTokens:
    providers: tuple[ProviderTokens, ...]

    def by_key(self, key: str) -> Optional[ProviderTokens]:
        for provider in self.providers:
            if provider.key == key:
                return provider
        return None

    def over_limit(
        self, limit: int = CONTEXT_TOKEN_LIMIT
    ) -> tuple[ProviderTokens, ...]:
        return tuple(provider for provider in self.providers if provider.tokens > limit)

    def log_line(self) -> str:
        parts = [f"{provider.label}: {provider.formatted()}" for provider in self.providers]
        return "🔢 Tokens do markdown — " + "; ".join(parts)

    def stats_block(self) -> str:
        return "\n".join(
            f"   • Tokens {provider.label}: {provider.formatted()}"
            for provider in self.providers
        )

    def markdown_bullets(self) -> list[str]:
        return [
            f"- **Tokens {provider.label}**: {provider.formatted()}"
            for provider in self.providers
        ]

    def summary_for(self, filename: str) -> str:
        parts = [f"{provider.label} {provider.tokens:,}" for provider in self.providers]
        return f"Tokens {filename}: " + "; ".join(parts)


@dataclass(frozen=True)
class KiwiEstimate:
    openai: int
    gemini: int
    log_path: Path
    sanitized_path: Path


@dataclass(frozen=True)
class KiwiLookup:
    sanitized_path: Optional[Path]
    estimate: Optional[KiwiEstimate]
    message: Optional[str]


@dataclass(frozen=True)
class Divergence:
    label: str
    estimated: int
    actual: int

    @property
    def ratio(self) -> float:
        if self.estimated <= 0:
            return 0.0
        return abs(self.actual - self.estimated) / self.estimated

    @property
    def exceeds(self) -> bool:
        return self.estimated > 0 and self.ratio > DIVERGENCE_RATIO

    def formatted(self) -> str:
        pct = self.ratio * 100
        return (
            f"{self.label}: estimativa do KiwiSplitter {self.estimated:,} tokens, "
            f"contagem real {self.actual:,} ({pct:.0f}% de diferença)"
        )


def count_markdown_tokens(text: str) -> MarkdownTokens:
    """Conta o markdown já gerado com os dois perfis usados pelo KiwiSplitter."""
    openai_tokens, openai_method = _openai_tokens(text or "")
    gemini_tokens, gemini_method = _gemini_tokens(text or "")
    return MarkdownTokens(
        providers=(
            ProviderTokens(_OPENAI, "OpenAI", openai_tokens, openai_method),
            ProviderTokens(_GEMINI, "Gemini", gemini_tokens, gemini_method),
        )
    )


def parse_kiwi_estimates(text: str) -> Optional[tuple[int, int]]:
    """Última estimativa de documentos selecionados gravada no log."""
    matches = list(_KIWI_ESTIMATE_RE.finditer(text or ""))
    if not matches:
        return None
    last = matches[-1]
    return int(last.group(1)), int(last.group(2))


def find_kiwi_estimate(
    pdf_path: Path, output_folder: Optional[Path] = None
) -> KiwiLookup:
    """
    Procura, na pasta do PDF e na pasta de destino, um arquivo
    ``*_full_sanitized*`` e a estimativa no log do KiwiSplitter.
    """
    pdf_path = Path(pdf_path)
    folders = _search_folders(pdf_path, output_folder)
    stem = _document_stem(pdf_path)
    if not stem:
        return KiwiLookup(None, None, None)

    sanitized = _newest(
        path
        for folder in folders
        for path in _iter_files(folder)
        if _is_sanitized_name(path.name, stem)
    )
    if sanitized is None:
        return KiwiLookup(None, None, None)

    log_folders = [sanitized.parent, *folders]
    logs = _newest_all(
        path
        for folder in log_folders
        for path in _iter_files(folder)
        if _is_kiwi_log_name(path.name, stem)
    )
    for log_path in logs:
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Não foi possível ler o log do KiwiSplitter: %s", log_path)
            continue
        parsed = parse_kiwi_estimates(content)
        if parsed is None:
            continue
        return KiwiLookup(
            sanitized_path=sanitized,
            estimate=KiwiEstimate(
                openai=parsed[0],
                gemini=parsed[1],
                log_path=log_path,
                sanitized_path=sanitized,
            ),
            message=None,
        )

    if logs:
        message = (
            f"Arquivo sanitizado do KiwiSplitter encontrado ({sanitized.name}), "
            f"mas o log {logs[0].name} não contém a contagem estimada de tokens."
        )
    else:
        message = (
            f"Arquivo sanitizado do KiwiSplitter encontrado ({sanitized.name}), "
            "mas não há log do KiwiSplitter (*log_split*.txt) na mesma pasta."
        )
    return KiwiLookup(sanitized, None, message)


def compare_with_kiwi(
    actual: MarkdownTokens, estimate: KiwiEstimate
) -> tuple[Divergence, ...]:
    pairs = (
        ("OpenAI", estimate.openai, _OPENAI),
        ("Gemini", estimate.gemini, _GEMINI),
    )
    found: list[Divergence] = []
    for label, estimated, key in pairs:
        provider = actual.by_key(key)
        if provider is None or estimated <= 0:
            continue
        found.append(
            Divergence(label=label, estimated=estimated, actual=provider.tokens)
        )
    return tuple(found)


def comparison_log_line(items: tuple[Divergence, ...]) -> Optional[str]:
    if not items:
        return None
    detail = "; ".join(item.formatted() for item in items)
    return f"Comparação com o log do KiwiSplitter — {detail}."


def context_window_alert(
    actual: MarkdownTokens,
    items: tuple[Divergence, ...] = (),
    *,
    limit: int = CONTEXT_TOKEN_LIMIT,
) -> Optional[str]:
    """Aviso quando a contagem real do markdown passa do limite de contexto."""
    over = actual.over_limit(limit)
    if not over:
        return None
    counted = "; ".join(f"{provider.label}: {provider.tokens:,}" for provider in over)
    lines = [
        (
            f"ALERTA: a contagem real do markdown ultrapassa {limit:,} tokens "
            f"({counted})."
        ),
        (
            "Há risco de exceder a janela de contexto do LLM usado para "
            "minutar a sentença."
        ),
        (
            "Use o KiwiSplitter novamente para excluir peças do processo, "
            "para não exceder a janela de contexto, e converta o PDF outra vez."
        ),
    ]
    diverged = [item for item in items if item.exceeds]
    if diverged:
        detail = "; ".join(item.formatted() for item in diverged)
        lines.append(
            "A estimativa do arquivo sanitizado no log do KiwiSplitter "
            f"diverge mais de {int(DIVERGENCE_RATIO * 100)}% da contagem real: {detail}."
        )
    return "\n".join(lines)


def _heuristic_tokens(text: str, provider: str) -> int:
    """Mesma heurística do KiwiSplitter para português jurídico."""
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return 0
    words = len(re.findall(r"\S+", clean))
    chars = len(clean)
    if provider == _GEMINI:
        return max(int(chars / 1.7), int(words * 1.8))
    return max(int(chars / 2.05), int(words * 1.5))


def _openai_tokens(text: str) -> tuple[int, str]:
    global _encoder, _encoder_failed
    if not text:
        return 0, "tiktoken cl100k"
    if not _encoder_failed:
        try:
            with _tokenizer_lock:
                if _encoder is None:
                    import tiktoken

                    _encoder = tiktoken.get_encoding("cl100k_base")
                encoder = _encoder
            return len(encoder.encode(text, disallowed_special=())), "tiktoken cl100k"
        except Exception:
            logger.warning("tiktoken indisponível; usando heurística PT-BR", exc_info=True)
            _encoder_failed = True
    return _heuristic_tokens(text, _OPENAI), "heurística PT-BR"


def _gemini_tokens(text: str) -> tuple[int, str]:
    if not text:
        return 0, "Gemma3"
    processor = _gemini_processor()
    if processor is None:
        return _heuristic_tokens(text, _GEMINI), "heurística PT-BR"
    try:
        with _tokenizer_lock:
            count = len(processor.EncodeAsIds(text))
        return int(round(count * AI_STUDIO_UPLOAD_MARGIN)), "Gemma3"
    except Exception:
        logger.warning("Falha no tokenizer Gemma3; usando heurística PT-BR", exc_info=True)
        return _heuristic_tokens(text, _GEMINI), "heurística PT-BR"


def _gemini_processor():
    global _sentencepiece, _sentencepiece_failed
    if _sentencepiece is not None:
        return _sentencepiece
    if _sentencepiece_failed:
        return None
    model_path = _gemma_model_path()
    if model_path is None:
        return None
    try:
        import sentencepiece as spm

        processor = spm.SentencePieceProcessor()
        if not processor.Load(str(model_path)):
            raise RuntimeError(f"Falha ao carregar {model_path}")
    except Exception:
        logger.warning("Tokenizer Gemini indisponível", exc_info=True)
        _sentencepiece_failed = True
        return None
    _sentencepiece = processor
    return processor


def _gemma_model_path() -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    path = (
        Path(local)
        / "Kiwi-Splitter"
        / "tokenizers"
        / "gemma3_cleaned_262144_v2.spiece.model"
    )
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def _document_stem(pdf_path: Path) -> str:
    stem = pdf_path.stem
    marker = "_full_sanitized"
    position = stem.lower().find(marker)
    if position > 0:
        return stem[:position].rstrip("_")
    return stem


def _search_folders(pdf_path: Path, output_folder: Optional[Path]) -> list[Path]:
    folders: list[Path] = []
    for candidate in (pdf_path.parent, output_folder):
        if candidate is None:
            continue
        folder = Path(candidate)
        if folder.is_dir() and folder not in folders:
            folders.append(folder)
    return folders


def _iter_files(folder: Path):
    try:
        for path in folder.iterdir():
            if path.is_file():
                yield path
    except OSError:
        logger.warning("Não foi possível listar a pasta %s", folder)


def _is_sanitized_name(name: str, stem: str) -> bool:
    return name.casefold().startswith(f"{stem}_full_sanitized".casefold())


def _is_kiwi_log_name(name: str, stem: str) -> bool:
    folded = name.casefold()
    return (
        folded.endswith(".txt")
        and "log_split" in folded
        and folded.startswith(f"{stem}_".casefold())
    )


def _newest(paths) -> Optional[Path]:
    found = [path for path in paths if path.is_file()]
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def _newest_all(paths) -> list[Path]:
    unique: dict[Path, Path] = {}
    for path in paths:
        if path.is_file():
            unique[path.resolve()] = path
    return sorted(unique.values(), key=lambda path: path.stat().st_mtime, reverse=True)
