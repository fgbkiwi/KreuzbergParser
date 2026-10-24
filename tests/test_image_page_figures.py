"""Decisão de legendas em image_page: uma JPEG versus várias fotos.

Não chama o Ollama. `_maybe_describe_image` e a extração de imagens são
substituídos; o OCR de cada figura também, para o teste não depender de
Tesseract nem de Kreuzberg.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Config, ProcessingMode
from core.form_templates import ExtractionResult
from core.kreuzberg_engine import KreuzbergOCREngine
from core.markdown_converter import MarkdownConverter
from core.page_classifier import PageClassification, PageImageInfo
from core.vlm_ocr import format_image_description_block

STAMP = "Fls.: 90\nPJe"
PAGE_TEXT = (
    "O reclamante trabalhou na cozinha industrial e registrou "
    "as condicoes do piso e dos equipamentos de higienizacao."
)
FIGURE_TEXT = (
    "Receituario afixado na parede do deposito com a lista de "
    "produtos de limpeza e as datas de validade visiveis."
)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _engine() -> KreuzbergOCREngine:
    engine = KreuzbergOCREngine.__new__(KreuzbergOCREngine)
    engine.config = Config()
    engine.mode = ProcessingMode.CPU
    engine.mode_config = Config.get_mode_config(ProcessingMode.CPU)
    engine.enable_template_extraction = False
    engine.enable_vlm_fallback = True
    engine.vlm_base_url = "http://127.0.0.1:11434/v1"
    engine.vlm_model = "qwen3-vl:8b-instruct"
    engine._layouts = {}
    return engine


def _image(
    num: int,
    *,
    x: float,
    y: float,
    width: int = 900,
    height: int = 700,
    page: int = 7,
) -> PageImageInfo:
    return PageImageInfo(
        page=page,
        num=num,
        img_type="image",
        width=width,
        height=height,
        encoding="jpeg",
        x=x,
        y=y,
        display_width=200.0,
        display_height=180.0,
        page_width=595.0,
        page_height=842.0,
    )


def _classification(images, *, kind=None, page: int = 7) -> PageClassification:
    content = [img for img in images if img.is_content_figure()]
    return PageClassification(
        page=page,
        page_class="image_page",
        subtype="image_page",
        residual_chars=0,
        raw_chars=0,
        max_image_coverage=0.4,
        image_count=len(content),
        strip_count=0,
        residual_text="",
        images=list(images),
        content_images=content,
        doc_kind=kind,
    )


class ImagePageFigureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.images_dir = Path(self.tmp.name)
        self.engine = _engine()
        self.calls = []
        self.extract_calls = []
        self.ocr_by_name = {}
        self.engine._maybe_describe_image = self._fake_describe
        self.engine._ocr_image_file = self._fake_ocr

    def _write(self, name: str, size: int = 4000) -> Path:
        path = self.images_dir / name
        path.write_bytes(b"\xff\xd8" + b"x" * max(0, size - 2))
        return path

    def _fake_describe(self, png_or_path, *, page, label="", index=None, total=None):
        self.calls.append(
            {
                "target": png_or_path,
                "page": page,
                "label": label,
                "index": index,
                "total": total,
            }
        )
        if isinstance(png_or_path, (bytes, bytearray)):
            return "raster da pagina"
        return f"figura {Path(png_or_path).name}"

    def _fake_ocr(self, path, *, prefer_tables, dpi_hint=300):
        _ = prefer_tables, dpi_hint
        text = self.ocr_by_name.get(Path(path).name, STAMP)
        return text, False, None, []

    def _patch_extract(self, saved):
        def fake_extract(*_args, **kwargs):
            self.extract_calls.append(kwargs)
            return list(saved)

        patcher = patch(
            "core.kreuzberg_engine.extract_page_images_to_dir",
            side_effect=fake_extract,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, images, saved, *, kind=None, ocr=STAMP, templates=False):
        self.engine.enable_template_extraction = templates
        self._patch_extract(saved)
        classification = _classification(images, kind=kind)
        result = self.engine._process_image_page(
            self.images_dir / "doc.pdf",
            classification,
            self.images_dir,
            raw_text="",
            png_bytes=PNG,
            raster_path=self.images_dir / "raster_page_0007.png",
            ocr_tuple=(ocr, False, "por", []),
        )
        return result

    def _assert_page_raster_only(self, result):
        self.assertEqual(len(self.calls), 1)
        self.assertIsInstance(self.calls[0]["target"], bytes)
        self.assertIsNone(self.calls[0]["index"])
        self.assertEqual(result["images_described"], 1)
        self.assertIn("### Descrição da imagem\nraster da pagina", result["text"])
        self.assertNotIn("### Descrição da imagem 1", result["text"])
        self.assertNotIn("### Descrição da imagem 2", result["text"])
        self.assertEqual(result["images"], [])
        self.assertEqual(result["extraction_source"], "image_describe")

    def test_zero_figures_keeps_single_page_caption(self):
        result = self._run([], [])
        self._assert_page_raster_only(result)
        self.assertEqual(self.extract_calls[0]["prefix"], "imgpage")
        self.assertTrue(self.extract_calls[0]["skip_logos"])
        self.assertEqual(self.extract_calls[0]["page_images"], [])

    def test_extract_failure_falls_back_to_page_caption(self):
        def boom(*_args, **_kwargs):
            raise OSError("pdfimages")

        patcher = patch(
            "core.kreuzberg_engine.extract_page_images_to_dir",
            side_effect=boom,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        images = [_image(0, x=40, y=200), _image(1, x=300, y=200)]
        result = self.engine._process_image_page(
            self.images_dir / "doc.pdf",
            _classification(images),
            self.images_dir,
            raw_text="",
            png_bytes=PNG,
            raster_path=self.images_dir / "raster_page_0007.png",
            ocr_tuple=(STAMP, False, "por", []),
        )
        self._assert_page_raster_only(result)

    def test_single_large_jpeg_is_not_described_twice(self):
        photo = _image(0, x=0, y=0, width=2480, height=3508)
        self.assertTrue(photo.is_full_page_scan())
        self.assertTrue(photo.is_content_figure())
        path = self._write("imgpage_0007-000.jpg")
        result = self._run([photo], [path])
        self._assert_page_raster_only(result)
        self.assertNotIn("figura imgpage", result["text"])
        targets = [call["target"] for call in self.calls]
        self.assertTrue(all(isinstance(item, bytes) for item in targets))

    def test_one_content_figure_plus_logo_or_tiny_file_stays_one_caption(self):
        photo = _image(1, x=40, y=220)
        logo = _image(0, x=10, y=12, width=80, height=80)
        # Faixa de assinatura: curta na página, alta demais para ser tira Gov.br.
        banner = _image(2, x=20, y=800, width=400, height=250)
        banner.display_height = 40
        self.assertTrue(logo.is_logo_or_icon())
        self.assertTrue(banner.is_banner_or_footer_image())
        self.assertFalse(banner.is_content_figure())
        paths = [
            self._write("imgpage_0007-000.jpg"),
            self._write("imgpage_0007-001.jpg"),
            self._write("imgpage_0007-002.jpg"),
            self._write("imgpage_0007-003.jpg", size=100),
        ]
        extra = _image(3, x=80, y=400)
        result = self._run([logo, photo, banner, extra], paths)
        self._assert_page_raster_only(result)
        self.assertNotIn("figura imgpage", result["text"])

    def test_two_figures_one_caption_each_in_reading_order(self):
        # Same Y: smaller X comes first. File 001 is to the left of file 000.
        right = _image(0, x=300, y=200)
        left = _image(1, x=40, y=200)
        right_path = self._write("imgpage_0007-000.jpg")
        left_path = self._write("imgpage_0007-001.jpg")
        result = self._run([right, left], [right_path, left_path])

        self.assertEqual(len(self.calls), 2)
        self.assertEqual([Path(call["target"]).name for call in self.calls], [
            "imgpage_0007-001.jpg",
            "imgpage_0007-000.jpg",
        ])
        self.assertEqual(
            [(call["index"], call["total"]) for call in self.calls],
            [(1, 2), (2, 2)],
        )
        self.assertTrue(all(isinstance(call["target"], Path) for call in self.calls))
        text = result["text"]
        self.assertLess(
            text.index("### Descrição da imagem 1"),
            text.index("### Descrição da imagem 2"),
        )
        self.assertIn(
            "### Descrição da imagem 1\nfigura imgpage_0007-001.jpg",
            text,
        )
        self.assertIn(
            "### Descrição da imagem 2\nfigura imgpage_0007-000.jpg",
            text,
        )
        self.assertNotIn("raster da pagina", text)
        self.assertEqual(result["images_described"], 2)
        self.assertEqual(result["extraction_source"], "image_describe")
        self.assertEqual(result["images"], [])
        self.assertFalse(result["ocr_failed"])

    def test_lower_figure_follows_higher_one(self):
        low = _image(0, x=10, y=520)
        high = _image(1, x=400, y=80)
        low_path = self._write("imgpage_0007-000.jpg")
        high_path = self._write("imgpage_0007-001.jpg")
        result = self._run([low, high], [low_path, high_path])
        names = [Path(call["target"]).name for call in self.calls]
        self.assertEqual(names, ["imgpage_0007-001.jpg", "imgpage_0007-000.jpg"])
        self.assertLess(
            result["text"].index("imgpage_0007-001.jpg"),
            result["text"].index("imgpage_0007-000.jpg"),
        )

    def test_usable_figure_ocr_is_kept_and_only_the_photo_is_described(self):
        high = _image(0, x=40, y=90)
        low = _image(1, x=40, y=460)
        high_path = self._write("imgpage_0007-000.jpg")
        low_path = self._write("imgpage_0007-001.jpg")
        self.ocr_by_name[high_path.name] = FIGURE_TEXT
        result = self._run([high, low], [high_path, low_path])

        self.assertEqual(len(self.calls), 1)
        self.assertEqual(Path(self.calls[0]["target"]).name, low_path.name)
        self.assertEqual((self.calls[0]["index"], self.calls[0]["total"]), (2, 2))
        text = result["text"]
        self.assertIn(FIGURE_TEXT, text)
        self.assertIn("### Descrição da imagem\nfigura imgpage_0007-001.jpg", text)
        self.assertNotIn("### Descrição da imagem 1", text)
        self.assertLess(text.index(FIGURE_TEXT), text.index("### Descrição da imagem"))
        self.assertEqual(result["images_described"], 1)
        self.assertNotIn("raster da pagina", text)

    def test_failed_captions_do_not_describe_the_whole_page(self):
        def no_caption(png_or_path, *, page, label="", index=None, total=None):
            self.calls.append(png_or_path)
            return None

        self.engine._maybe_describe_image = no_caption
        paths = [
            self._write("imgpage_0007-000.jpg"),
            self._write("imgpage_0007-001.jpg"),
        ]
        images = [_image(0, x=40, y=200), _image(1, x=300, y=200)]
        result = self._run(images, paths)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(isinstance(item, Path) for item in self.calls))
        self.assertEqual(result["images_described"], 0)
        self.assertNotIn("Descrição da imagem", result["text"])

    def test_usable_page_ocr_skips_photo_captions(self):
        images = [_image(0, x=40, y=200), _image(1, x=300, y=200)]
        paths = [
            self._write("imgpage_0007-000.jpg"),
            self._write("imgpage_0007-001.jpg"),
        ]
        result = self._run(images, paths, ocr=PAGE_TEXT)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.extract_calls, [])
        self.assertIn("cozinha industrial", result["text"])
        self.assertNotIn("Descrição da imagem", result["text"])
        self.assertEqual(result["images_described"], 0)

    def test_structured_form_does_not_describe_figures(self):
        images = [_image(0, x=40, y=200), _image(1, x=300, y=200)]
        paths = [
            self._write("imgpage_0007-000.jpg"),
            self._write("imgpage_0007-001.jpg"),
        ]
        for source in ("template", "vlm"):
            with self.subTest(source=source):
                self.calls.clear()
                self.extract_calls.clear()
                structured = ExtractionResult(
                    kind="trct",
                    markdown="TRCT",
                    source=source,
                    ok=True,
                    fill_ratio=1.0,
                )
                with patch(
                    "core.kreuzberg_engine.try_structured_extraction",
                    return_value=structured,
                ):
                    result = self._run(images, paths, kind="trct", templates=True)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.extract_calls, [])
                self.assertEqual(result["text"], "TRCT")
                self.assertEqual(result["extraction_source"], source)
                self.assertEqual(result["images_described"], 0)

    def test_without_vlm_current_behavior_does_not_extract_figures(self):
        self.engine.enable_vlm_fallback = False
        self.engine._maybe_describe_image = (
            KreuzbergOCREngine._maybe_describe_image.__get__(self.engine)
        )
        images = [_image(0, x=40, y=200), _image(1, x=300, y=200)]

        def extract_should_not_run(*_args, **_kwargs):
            raise AssertionError("extração não deveria rodar sem VLM")

        with patch(
            "core.kreuzberg_engine.extract_page_images_to_dir",
            side_effect=extract_should_not_run,
        ), patch("core.vlm_ocr.describe_image") as describe:
            result = self.engine._process_image_page(
                self.images_dir / "doc.pdf",
                _classification(images),
                self.images_dir,
                raw_text="",
                png_bytes=PNG,
                raster_path=self.images_dir / "raster_page_0007.png",
                ocr_tuple=(STAMP, False, "por", []),
            )
        describe.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertNotIn("Descrição da imagem", result["text"])
        self.assertEqual(result["images_described"], 0)

    def test_log_numbers_the_figure_being_described(self):
        engine = _engine()
        with patch("core.vlm_ocr.describe_image", return_value="uma foto") as describe:
            with self.assertLogs("core.kreuzberg_engine", level="INFO") as captured:
                text = engine._maybe_describe_image(
                    PNG,
                    page=12,
                    label="imgpage_0012-001.jpg",
                    index=2,
                    total=3,
                )
        self.assertEqual(text, "uma foto")
        describe.assert_called_once()
        self.assertTrue(
            any(
                "Página 12: descrevendo imagem 2/3 via VLM" in line
                and "imgpage_0012-001.jpg" in line
                for line in captured.output
            )
        )


class DescriptionHeadingTests(unittest.TestCase):
    def test_single_heading_stays_unnumbered(self):
        self.assertEqual(
            format_image_description_block("Sala com piso molhado."),
            "### Descrição da imagem\nSala com piso molhado.",
        )
        self.assertEqual(
            format_image_description_block(
                "### Descrição da imagem\nSala com piso molhado."
            ),
            "### Descrição da imagem\nSala com piso molhado.",
        )

    def test_numbered_heading_when_index_is_set(self):
        self.assertEqual(
            format_image_description_block("Armário aberto.", index=2),
            "### Descrição da imagem 2\nArmário aberto.",
        )

    def test_markdown_converter_keeps_description_headings(self):
        converter = MarkdownConverter(config=Config())
        body = converter._clean_page_body(
            "\n".join(
                [
                    "### Descrição da imagem",
                    "Primeira foto.",
                    "",
                    "### Descrição da imagem 1",
                    "Segunda foto.",
                    "",
                    "### Descrição da imagem 2",
                    "Terceira foto.",
                    "",
                    "### Quadro / figura 3",
                    "legado que deve sair",
                ]
            ),
            keep_signature=False,
            signature_lines=[],
        )
        self.assertIn("### Descrição da imagem", body)
        self.assertIn("### Descrição da imagem 1", body)
        self.assertIn("### Descrição da imagem 2", body)
        self.assertIn("Primeira foto.", body)
        self.assertIn("Terceira foto.", body)
        self.assertNotIn("### Quadro / figura 3", body)


if __name__ == "__main__":
    unittest.main()
