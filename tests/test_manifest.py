"""Manifesto dos autos (core/manifest.py)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.manifest import build_manifest, manifest_path_for, write_manifest
from core.pje_sumario import SumarioEntry

SUMARIO = {
    "a1b2c3d": SumarioEntry("a1b2c3d", "Petição Inicial", "Petição Inicial", "10/01/2025 10:00"),
    "c3d4e5f": SumarioEntry("c3d4e5f", "Cartões de ponto", "Controle de Frequência", "20/02/2025 11:31"),
}
RESULT = {
    "metadata": {"filename": "autos.pdf", "processo": "0000456-78.2025.5.10.0009"},
    "pages": [
        {"page": 1, "fls": 2, "type": "native", "signature_ids": ["A1B2C3D"], "text": "Inicial"},
        {"page": 2, "fls": 3, "type": "image_page", "signature_ids": ["c3d4e5f"],
         "ocr_failed": True, "text": ""},
        {"page": 3, "fls": 4, "type": "native", "signature_ids": ["fffffff"], "text": "Sem sumário",
         "sumario_tipo": "Documento Diverso", "sumario_titulo": "Anexo"},
        {"page": 4, "fls": 5, "type": "native", "signature_ids": [], "text": (
            "SUMÁRIO\n"
            "a1b2c3d  10/01/2025 10:00   Petição Inicial      Petição Inicial\n"
            "c3d4e5f  20/02/2025 11:31   Cartões de ponto     Controle de Frequência\n"
            "d4e5f6a  05/05/2025 14:00   Ata                  Ata da Audiência\n")},
    ],
}


def test_manifesto_liga_cada_pagina_ao_documento_do_sumario():
    m = build_manifest(RESULT, SUMARIO)
    assert m["versao"] == 1 and m["total_paginas"] == 4 and m["documentos_no_sumario"] == 2
    p1, p2, p3, p4 = m["paginas"]
    assert (p1["doc_id"], p1["tipo"], p1["data"]) == ("a1b2c3d", "Petição Inicial", "10/01/2025 10:00")
    assert p2["titulo"] == "Cartões de ponto" and p2["ocr_falhou"] and p2["classe"] == "image_page"
    # ID fora do sumário: mantém o ID da assinatura e o que a classificação já sabia
    assert (p3["doc_id"], p3["tipo"], p3["titulo"]) == ("fffffff", "Documento Diverso", "Anexo")
    assert p4["sumario"] and not p1["sumario"] and p4["doc_id"] is None


def test_manifesto_sem_sumario_e_gravacao(tmp_path):
    m = build_manifest(RESULT)
    assert m["paginas"][0]["doc_id"] == "a1b2c3d" and m["paginas"][0]["tipo"] is None
    md = tmp_path / "autos_ocr_cpu.md"
    caminho = write_manifest(RESULT, md, SUMARIO)
    assert caminho == manifest_path_for(md) == tmp_path / "autos_ocr_cpu.manifesto.json"
    assert json.loads(caminho.read_text(encoding="utf-8"))["processo"] == "0000456-78.2025.5.10.0009"
