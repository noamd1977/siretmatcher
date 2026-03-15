"""Tests du module lookups — chargement JSON, comptage, reload, fallback."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from siret_matcher import lookups
from siret_matcher.lookups import (
    DEPT_TO_REGION,
    ENSEIGNE_TO_OPCO,
    IDCC_TO_CONVENTION,
    NAF_LIBELLES,
    NAF_TO_OPCO,
    TRANCHE_MAP,
    reload_lookups,
)


# ---- Chargement sans erreur ----

class TestLoading:
    def test_dept_to_region_loaded(self):
        assert isinstance(DEPT_TO_REGION, dict)
        assert len(DEPT_TO_REGION) > 0

    def test_idcc_to_convention_loaded(self):
        assert isinstance(IDCC_TO_CONVENTION, dict)
        assert len(IDCC_TO_CONVENTION) > 0

    def test_naf_libelles_loaded(self):
        assert isinstance(NAF_LIBELLES, dict)
        assert len(NAF_LIBELLES) > 0

    def test_naf_to_opco_loaded(self):
        assert isinstance(NAF_TO_OPCO, dict)
        assert len(NAF_TO_OPCO) > 0

    def test_enseigne_to_opco_loaded(self):
        assert isinstance(ENSEIGNE_TO_OPCO, dict)
        assert len(ENSEIGNE_TO_OPCO) > 0

    def test_tranche_map_loaded(self):
        assert isinstance(TRANCHE_MAP, dict)
        assert len(TRANCHE_MAP) > 0


# ---- Nombre d'entrées attendu ----

class TestEntryCounts:
    def test_dept_to_region_count(self):
        assert len(DEPT_TO_REGION) == 102

    def test_idcc_to_convention_count(self):
        assert len(IDCC_TO_CONVENTION) == 495

    def test_naf_libelles_count(self):
        assert len(NAF_LIBELLES) == 462

    def test_naf_to_opco_count(self):
        assert len(NAF_TO_OPCO) == 53

    def test_enseigne_to_opco_count(self):
        assert len(ENSEIGNE_TO_OPCO) == 27

    def test_tranche_map_count(self):
        assert len(TRANCHE_MAP) == 16


# ---- Contenu cohérent ----

class TestContent:
    def test_dept_paris(self):
        assert DEPT_TO_REGION["75"] == "Île-de-France"

    def test_dept_corse(self):
        assert DEPT_TO_REGION["2A"] == "Corse"

    def test_dept_dom(self):
        assert DEPT_TO_REGION["974"] == "La Réunion"

    def test_naf_coiffure(self):
        assert "96.02A" in NAF_LIBELLES
        assert "Coiffure" in NAF_LIBELLES["96.02A"]

    def test_opco_auto(self):
        assert NAF_TO_OPCO["45"] == "OPCO Mobilités"

    def test_enseigne_speedy(self):
        assert ENSEIGNE_TO_OPCO["speedy"] == "OPCO Mobilités"

    def test_tranche_20_49(self):
        assert TRANCHE_MAP["12"] == "20-49"


# ---- reload_lookups() ----

class TestReload:
    def test_reload_restores_data(self):
        """reload_lookups() recharge les données depuis les fichiers."""
        original_count = len(lookups.DEPT_TO_REGION)
        # Vider le dict
        lookups.DEPT_TO_REGION.clear()
        assert len(lookups.DEPT_TO_REGION) == 0
        # Recharger
        reload_lookups()
        assert len(lookups.DEPT_TO_REGION) == original_count


# ---- Fichier manquant → warning + dict vide ----

class TestMissingFile:
    def test_missing_json_returns_empty_dict(self, tmp_path):
        """Un fichier JSON manquant ne crashe pas, retourne {} avec warning."""
        with patch.object(lookups, "_DATA_DIR", tmp_path):
            result = lookups._load("nonexistent")
        assert result == {}

    def test_missing_json_logs_warning(self, tmp_path, caplog):
        """Un fichier JSON manquant logge un warning."""
        import logging
        with caplog.at_level(logging.WARNING):
            with patch.object(lookups, "_DATA_DIR", tmp_path):
                lookups._load("nonexistent")
        assert "manquant" in caplog.text


# ---- Fichiers JSON valides ----

class TestJsonFiles:
    """Vérifie que chaque fichier JSON est bien formé."""

    DATA_DIR = Path(__file__).resolve().parent.parent / "config" / "data"

    @pytest.mark.parametrize("filename", [
        "dept_to_region",
        "idcc_to_convention",
        "naf_libelles",
        "naf_to_opco",
        "enseigne_to_opco",
        "tranche_map",
    ])
    def test_json_valid(self, filename):
        path = self.DATA_DIR / f"{filename}.json"
        assert path.exists(), f"{path} n'existe pas"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) > 0

    @pytest.mark.parametrize("filename", [
        "dept_to_region",
        "idcc_to_convention",
        "naf_libelles",
        "naf_to_opco",
        "enseigne_to_opco",
        "tranche_map",
    ])
    def test_json_all_string_values(self, filename):
        """Toutes les valeurs doivent être des strings."""
        path = self.DATA_DIR / f"{filename}.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key, value in data.items():
            assert isinstance(key, str), f"Clé non-string: {key}"
            assert isinstance(value, str), f"Valeur non-string pour {key}: {value}"
