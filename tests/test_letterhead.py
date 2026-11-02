"""Corte de timbre não apaga intimação nem ato judicial."""
from __future__ import annotations

import unittest

from core.letterhead import apply_letterhead_strip
from core.page_classifier import PageClassification
from core.pje_sumario import (
    SumarioEntry,
    extract_signature_ids,
    extract_stamp_document_ids,
    is_letterhead_preserved_tipo,
    resolve_entry_for_page,
)


def _page(
    number: int,
    text: str,
    *,
    tipo: str | None = None,
    exempt: bool = False,
) -> PageClassification:
    return PageClassification(
        page=number,
        page_class="native",
        subtype="native_text",
        residual_chars=len(text),
        raw_chars=len(text),
        max_image_coverage=0.0,
        image_count=0,
        strip_count=0,
        residual_text=text,
        sumario_tipo=tipo,
        letterhead_exempt=exempt,
    )


class LetterheadPreservationTests(unittest.TestCase):
    def test_tipo_de_ato_e_intimacao_sao_preservados(self):
        for tipo in (
            "Intimação",
            "intimacao",
            "Despacho",
            "Decisão",
            "Decisão (cópia)",
            "Sentença",
            "Sentença de embargos",
        ):
            self.assertTrue(is_letterhead_preserved_tipo(tipo), tipo)
        for tipo in ("Manifestação", "Petição Inicial", "Certidão", "Acórdão", ""):
            self.assertFalse(is_letterhead_preserved_tipo(tipo), tipo)

    def test_carimbo_antigo_nao_captura_juntado(self):
        old = (
            "Assinado eletronicamente por: FERNANDO GABRIELE BERNARDES - "
            "Juntado em: 19/07/2022 15:35:07 - f744e07"
        )
        new = (
            "Documento assinado eletronicamente por FERNANDO GABRIELE BERNARDES, "
            "em 19/02/2025, às 23:10:58 - 827a9ce"
        )
        self.assertEqual(extract_signature_ids(old), [])
        self.assertEqual(extract_stamp_document_ids(old), ["f744e07"])
        self.assertEqual(extract_stamp_document_ids(new), ["827a9ce"])

    def test_muitas_intimacoes_nao_cortam_decisao_nem_o_fecho(self):
        dispositivo = "\n".join(
            [
                "2. No mesmo prazo deverá, a parte reclamante, apresentar uma conta",
                "3. Cumpra-se via sistema PJ-e.",
                "4. Expeça-se o competente RPV ou Ofício Precatório.",
                "BRASILIA/DF, 19 de fevereiro de 2025.",
                "FERNANDO GABRIELE BERNARDES",
                "Juiz do Trabalho Titular",
            ]
        )
        decisao = _page(
            1,
            "DECISÃO\nHomologo a conta.\n" + dispositivo,
            tipo="Decisão",
        )
        intimacoes = [
            _page(2 + n, "Fica V. Sa. intimado.\n" + dispositivo, tipo="Intimação")
            for n in range(5)
        ]
        timbre = "Timbre repetido do escritorio de peticionamento"
        rodape = "Rodape repetido do escritorio de peticionamento"
        manifestacoes = [
            _page(
                10 + n,
                f"{timbre}\nTexto proprio da manifestacao {n} com conteudo distinto.\n{rodape}",
                tipo="Manifestação",
            )
            for n in range(3)
        ]
        pages = [decisao, *intimacoes, *manifestacoes]
        apply_letterhead_strip(pages, min_repeat_pages=3)

        self.assertIn("Juiz do Trabalho Titular", decisao.residual_text)
        self.assertIn("Cumpra-se via sistema PJ-e.", decisao.residual_text)
        self.assertIn("FERNANDO GABRIELE BERNARDES", decisao.residual_text)
        for page in intimacoes:
            self.assertIn("Juiz do Trabalho Titular", page.residual_text)
            self.assertIn("Cumpra-se via sistema PJ-e.", page.residual_text)
        for page in manifestacoes:
            self.assertNotIn(timbre, page.residual_text)
            self.assertNotIn(rodape, page.residual_text)
            self.assertIn("Texto proprio da manifestacao", page.residual_text)

    def test_carimbo_antigo_de_despacho_fica_isento_sem_mudar_o_ocr(self):
        entries = {
            "f744e07": SumarioEntry("f744e07", "Despacho", "Despacho"),
        }
        text = (
            "DESPACHO\nPublique-se.\n"
            "Assinado eletronicamente por: FERNANDO GABRIELE BERNARDES - "
            "Juntado em: 19/07/2022 15:35:07 - f744e07"
        )
        official = resolve_entry_for_page(extract_signature_ids(text), entries)
        preserved = official or resolve_entry_for_page(
            extract_stamp_document_ids(text), entries
        )
        self.assertIsNone(official)
        self.assertIsNotNone(preserved)
        self.assertTrue(is_letterhead_preserved_tipo(preserved.tipo))


if __name__ == "__main__":
    unittest.main()
