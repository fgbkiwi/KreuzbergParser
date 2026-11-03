"""RapidOCR reconstruction and Portuguese-safe classifier settings."""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from core.rapid_ocr import _boxes_to_text, _output_to_tuple, _rapidocr_params


class RapidOcrParamsTests(unittest.TestCase):
    def test_angle_classifier_is_disabled(self):
        params = _rapidocr_params(rec_batch_num=8, model_dir=Path("models"))
        self.assertFalse(params["Global.use_cls"])


class ReadingOrderTests(unittest.TestCase):
    def test_paragraph_lines_keep_vertical_order(self):
        boxes = np.array(
            [
                [[10, 80], [400, 80], [400, 100], [10, 100]],
                [[10, 40], [400, 40], [400, 60], [10, 60]],
                [[10, 120], [400, 120], [400, 140], [10, 140]],
            ],
            dtype=float,
        )
        txts = (
            "conjunto ou separadamente",
            "5584/70, confessar, desistir, transigir",
            "dando tudo por bom, firme e valioso",
        )
        text = _boxes_to_text(boxes, txts)
        self.assertEqual(
            text.splitlines(),
            [
                "5584/70, confessar, desistir, transigir",
                "conjunto ou separadamente",
                "dando tudo por bom, firme e valioso",
            ],
        )

    def test_same_row_is_left_to_right(self):
        boxes = np.array(
            [
                [[300, 10], [380, 10], [380, 30], [300, 30]],
                [[20, 12], [90, 12], [90, 32], [20, 32]],
                [[150, 11], [240, 11], [240, 31], [150, 31]],
            ],
            dtype=float,
        )
        text = _boxes_to_text(boxes, ("SÃO PAULO", "BRASÍLIA", "CAMPINAS"))
        self.assertEqual(text, "BRASÍLIA CAMPINAS SÃO PAULO")

    def test_empty_snippets_are_skipped_without_dropping_neighbors(self):
        boxes = np.array(
            [
                [[10, 10], [100, 10], [100, 30], [10, 30]],
                [[10, 40], [100, 40], [100, 60], [10, 60]],
                [[10, 70], [100, 70], [100, 90], [10, 90]],
            ],
            dtype=float,
        )
        text = _boxes_to_text(boxes, ("uma", "", "três"))
        self.assertEqual(text.splitlines(), ["uma", "três"])

    def test_output_tuple_uses_boxes_when_present(self):
        class Out:
            boxes = np.array(
                [
                    [[10, 50], [80, 50], [80, 70], [10, 70]],
                    [[10, 10], [80, 10], [80, 30], [10, 30]],
                ],
                dtype=float,
            )
            txts = ("segunda", "primeira")

        text, has_tables, lang, tables = _output_to_tuple(Out())
        self.assertEqual(text, "primeira\nsegunda")
        self.assertFalse(has_tables)
        self.assertEqual(lang, "pt")
        self.assertEqual(tables, [])


if __name__ == "__main__":
    unittest.main()
