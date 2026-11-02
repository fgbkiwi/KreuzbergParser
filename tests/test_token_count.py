"""Contagem de tokens do markdown e comparação com o log do KiwiSplitter."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from config import Config
from core.markdown_converter import MarkdownConverter
from utils.token_count import (
    CONTEXT_TOKEN_LIMIT,
    Divergence,
    MarkdownTokens,
    ProviderTokens,
    compare_with_kiwi,
    context_window_alert,
    count_markdown_tokens,
    find_kiwi_estimate,
    parse_kiwi_estimates,
)


def _tokens(openai: int, gemini: int) -> MarkdownTokens:
    return MarkdownTokens(
        providers=(
            ProviderTokens("openai", "OpenAI", openai, "tiktoken cl100k"),
            ProviderTokens("gemini", "Gemini", gemini, "Gemma3"),
        )
    )


class ParseKiwiLogTests(unittest.TestCase):
    def test_uses_the_last_selected_estimate(self):
        text = (
            "Total estimado (documentos selecionados / arquivo sanitizado): "
            "openai=100 gemini=200\n"
            "Total estimado (documentos selecionados / arquivo sanitizado): "
            "openai=300 gemini=400\n"
        )
        self.assertEqual(parse_kiwi_estimates(text), (300, 400))

    def test_missing_estimate_returns_none(self):
        text = "Método de contagem de tokens: gemini=sentencepiece:gemma3"
        self.assertIsNone(parse_kiwi_estimates(text))


class ContextAlertTests(unittest.TestCase):
    def test_warns_whenever_real_count_exceeds_limit(self):
        actual = _tokens(CONTEXT_TOKEN_LIMIT + 1, 10)
        alert = context_window_alert(actual)
        self.assertIsNotNone(alert)
        self.assertIn("KiwiSplitter", alert)
        self.assertIn("excluir peças", alert)
        self.assertIn("janela de contexto", alert)

    def test_limit_itself_does_not_warn(self):
        actual = _tokens(CONTEXT_TOKEN_LIMIT, CONTEXT_TOKEN_LIMIT)
        self.assertIsNone(context_window_alert(actual))

    def test_divergence_alone_does_not_raise_context_alert(self):
        actual = _tokens(130, 100)
        items = compare_with_kiwi(
            actual,
            type("E", (), {"openai": 100, "gemini": 100})(),
        )
        self.assertTrue(items[0].exceeds)
        self.assertIsNone(context_window_alert(actual, items))

    def test_alert_mentions_divergence_above_the_limit(self):
        actual = _tokens(CONTEXT_TOKEN_LIMIT + 50_000, 10)
        items = (
            Divergence(
                label="OpenAI",
                estimated=100_000,
                actual=CONTEXT_TOKEN_LIMIT + 50_000,
            ),
        )
        alert = context_window_alert(actual, items)
        self.assertIn("diverge mais de 20%", alert)
        self.assertIn("100,000", alert)


class KiwiFileLookupTests(unittest.TestCase):
    def test_reads_estimate_next_to_sanitized_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stem = "Processo_0001"
            pdf = root / f"{stem}.pdf"
            pdf.write_bytes(b"%PDF")
            sanitized = root / f"{stem}_full_sanitized_20261024.pdf"
            sanitized.write_bytes(b"%PDF")
            log = root / f"{stem}_log_split_20261024.txt"
            log.write_text(
                "Total estimado (documentos selecionados / arquivo sanitizado): "
                "openai=1000 gemini=1200\n",
                encoding="utf-8",
            )
            found = find_kiwi_estimate(pdf, root)
            self.assertEqual(found.sanitized_path, sanitized)
            self.assertIsNotNone(found.estimate)
            self.assertEqual(found.estimate.openai, 1000)
            self.assertEqual(found.estimate.gemini, 1200)
            self.assertIsNone(found.message)

    def test_reports_sanitized_file_without_estimate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stem = "Processo_0002"
            pdf = root / f"{stem}.pdf"
            pdf.write_bytes(b"%PDF")
            (root / f"{stem}_full_sanitized_20261024.pdf").write_bytes(b"%PDF")
            (root / f"{stem}_log_split_20261024.txt").write_text(
                "Estimativa de tokens baseada no texto embutido do PDF.\n",
                encoding="utf-8",
            )
            found = find_kiwi_estimate(pdf, None)
            self.assertIsNone(found.estimate)
            self.assertIn("não contém a contagem estimada", found.message or "")


class MarkdownTokenSectionTests(unittest.TestCase):
    def test_statistics_include_token_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "saida.md"
            converter = MarkdownConverter(config=Config())
            converter.convert_to_markdown(
                {
                    "pages": [
                        {
                            "page": 1,
                            "text": "Texto de teste da sentença trabalhista.",
                            "type": "native",
                        }
                    ],
                    "statistics": {
                        "total_pages": 1,
                        "total_time": 0.2,
                        "avg_time_per_page": 0.2,
                        "processing_speed": "1 pág/s",
                        "native_pages": 1,
                        "hybrid_pages": 0,
                        "image_pages": 0,
                        "pages_with_tables": 0,
                        "total_words": 5,
                        "total_characters": 40,
                    },
                    "metadata": {
                        "filename": "teste.pdf",
                        "total_pages": 1,
                        "mode": "cpu",
                        "backend": "teste",
                        "processing_time": 0.2,
                    },
                },
                str(target),
            )
            text = target.read_text(encoding="utf-8")
            self.assertIn("**Tokens OpenAI**", text)
            self.assertIn("**Tokens Gemini**", text)
            self.assertIsNotNone(converter.last_token_count)
            self.assertGreater(converter.last_token_count.by_key("openai").tokens, 0)


class CountMarkdownTests(unittest.TestCase):
    def test_counts_non_empty_text(self):
        counted = count_markdown_tokens("Petição inicial com pedido de horas extras.")
        self.assertGreater(counted.by_key("openai").tokens, 0)
        self.assertGreater(counted.by_key("gemini").tokens, 0)
        self.assertIn("Tokens do markdown", counted.log_line())


if __name__ == "__main__":
    unittest.main()
