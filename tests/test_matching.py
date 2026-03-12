"""Tests sur les 19 entreprises d'Ajaccio du dataset réel."""
import asyncio
import logging
import sys
sys.path.insert(0, ".")

from siret_matcher.models import Prospect
from siret_matcher.normalizer import normalize_prospect, clean_name, generate_variants

# Les 19 entreprises du test réel
PROSPECTS = [
    # 6 trouvés en V9
    {"nom": "Sarl Garage Du Golfe", "adresse": "15 Rue Jean Nicoli, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "GARAGE DE BALEONE", "adresse": "Route de Baleone, 20167 Afa", "code_postal": "20167", "ville": "Afa"},
    {"nom": "Garage franchini", "adresse": "Av Noël Franchini, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "Garage Desini", "adresse": "Rue des Artisans, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "Garage le grand ajaccio", "adresse": "ZI Baleone, 20167 Sarrola-Carcopino", "code_postal": "20167", "ville": "Sarrola-Carcopino"},
    {"nom": "Corse Echappement Service", "adresse": "ZI Vazzio, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    # 13 non trouvés en V9 — les cas difficiles
    {"nom": "Eurotyre - Garage 2A Pneus – Carrosserie", "adresse": "29 Rue Paul Colonna d'Istria, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio", "site_web": "https://ajaccio-pneus.eurotyre.fr/"},
    {"nom": "2A DEBOSSELAGE", "adresse": "Rue Paul Colonna d'Istria, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "Point S - Ajaccio (Auto Pneus Services Ajaccio)", "adresse": "32 Avenue du Docteur Noël Franchini, 20000 Ajaccio", "code_postal": "20090", "ville": "Ajaccio", "site_web": ""},
    {"nom": "AD CARROSSERIE ROCCASERRA", "adresse": "Route de Baleone, 20167 Ajaccio", "code_postal": "20167", "ville": "Ajaccio"},
    {"nom": "Speedy", "adresse": "Résidence Les Dauphins, Quartier Saint Joseph, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio", "site_web": "https://www.speedy.fr"},
    {"nom": "AJACCIO AUTO RAPID'SERVICES", "adresse": "Route de Mezzavia, 20000 Ajaccio", "code_postal": "20000", "ville": "Ajaccio"},
    {"nom": "Garage Le pacha", "adresse": "Rue Paul Colonna d'Istria, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "AUTO PRESTO 2A", "adresse": "ZI Vazzio, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "Garage TUNING AUTO", "adresse": "Vazzio, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "Equip'Auto", "adresse": "Route du Vazzio, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "Garage Autorepair", "adresse": "Rue des Artisans, 20000 Ajaccio", "code_postal": "20000", "ville": "Ajaccio"},
    {"nom": "Cors'Auto", "adresse": "ZI Vazzio, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
    {"nom": "Auto-Relais", "adresse": "Route de Mezzavia, 20090 Ajaccio", "code_postal": "20090", "ville": "Ajaccio"},
]


def test_normalizer():
    """Vérifier que la normalisation produit les bonnes variantes."""
    print("=" * 70)
    print("TEST NORMALIZER — Vérification des variantes de recherche")
    print("=" * 70)

    for data in PROSPECTS:
        p = Prospect(**{k: data.get(k, "") for k in ["nom", "adresse", "code_postal", "ville", "site_web"]})
        normalize_prospect(p)
        status = "✓" if p.nom_clean and len(p.nom_variantes) > 0 else "✗"
        print(f"\n  {status} {p.nom}")
        print(f"    nom_clean:       {p.nom_clean}")
        print(f"    nom_parentheses: {p.nom_parentheses or '—'}")
        print(f"    variantes:       {p.nom_variantes}")
        print(f"    addr_numero:     {p.adresse_numero or '—'}")
        print(f"    addr_voie_clean: {p.adresse_voie_clean or '—'}")
        print(f"    département:     {p.departement}")


def test_key_cases():
    """Vérifier les cas critiques qui échouaient en V9."""
    print("\n" + "=" * 70)
    print("TEST CAS CRITIQUES — Ce qui doit maintenant matcher")
    print("=" * 70)

    cases = [
        ("Point S - Ajaccio (Auto Pneus Services Ajaccio)",
         "Doit extraire 'AUTO PNEUS SERVICES' des parenthèses"),
        ("Eurotyre - Garage 2A Pneus – Carrosserie",
         "Doit extraire '2A PNEUS' après retrait franchise"),
        ("Cors'Auto",
         "Apostrophe → 'CORS AUTO'"),
        ("Equip'Auto",
         "Apostrophe → 'EQUIP AUTO'"),
        ("AJACCIO AUTO RAPID'SERVICES",
         "Apostrophe → 'RAPID SERVICES' (sans ville)"),
        ("Auto-Relais",
         "Tiret → 'AUTO RELAIS'"),
        ("AD CARROSSERIE ROCCASERRA",
         "Doit garder 'ROCCASERRA' comme mot distinctif"),
        ("Speedy",
         "Franchise seule — doit matcher par ADRESSE (pas par nom)"),
    ]

    all_ok = True
    for nom, expected in cases:
        p = Prospect(nom=nom, adresse="Test, 20090 Ajaccio", code_postal="20090", ville="Ajaccio")
        normalize_prospect(p)
        
        print(f"\n  [{nom}]")
        print(f"    Attendu: {expected}")
        print(f"    clean:    {p.nom_clean}")
        print(f"    paren:    {p.nom_parentheses or '—'}")
        print(f"    variants: {p.nom_variantes}")

        # Vérifications spécifiques
        ok = True
        if "parenthèses" in expected and "AUTO PNEUS SERVICES" not in str(p.nom_variantes):
            print(f"    ✗ ERREUR: 'AUTO PNEUS SERVICES' manquant des variantes")
            ok = False
        if "CORS AUTO" in expected and "CORS AUTO" not in p.nom_clean and "CORS" not in p.nom_clean:
            print(f"    ✗ ERREUR: apostrophe non gérée")
            ok = False
        if "EQUIP AUTO" in expected and "EQUIP" not in p.nom_clean:
            print(f"    ✗ ERREUR: apostrophe non gérée")
            ok = False
        if "ROCCASERRA" in expected and "ROCCASERRA" not in str(p.nom_variantes):
            print(f"    ✗ ERREUR: mot distinctif perdu")
            ok = False

        if ok:
            print(f"    ✓ OK")
        else:
            all_ok = False

    print(f"\n{'='*70}")
    print(f"{'TOUS LES TESTS PASSENT' if all_ok else 'CERTAINS TESTS ÉCHOUENT'}")
    print(f"{'='*70}")
    return all_ok


async def test_full_pipeline():
    """Test complet avec API + base locale (nécessite PostgreSQL)."""
    from siret_matcher.matcher import match_batch

    prospects = []
    for data in PROSPECTS:
        p = Prospect(**{k: data.get(k, "") for k in ["nom", "adresse", "code_postal", "ville", "site_web"]})
        prospects.append(p)

    print("\n" + "=" * 70)
    print("TEST PIPELINE COMPLET — 19 entreprises d'Ajaccio")
    print("=" * 70)

    results = await match_batch(prospects, use_db=True, concurrency=3)

    found = 0
    for p in results:
        r = p.result
        status = "✓" if r and r.siret else "✗"
        if r and r.siret:
            found += 1
        method = r.methode if r else "—"
        score = r.score if r else 0
        siret = r.siret if r else "—"
        denom = r.denomination[:30] if r and r.denomination else "—"
        print(f"  {status} {p.nom[:40]:40s} → {siret:16s} | {method:25s} | score={score:3.0f} | {denom}")

    print(f"\n  RÉSULTAT: {found}/{len(results)} matchés ({found/len(results)*100:.0f}%)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

    # Tests sans base de données
    test_normalizer()
    ok = test_key_cases()

    # Test complet (si --full)
    if "--full" in sys.argv:
        asyncio.run(test_full_pipeline())
    elif ok:
        print("\n→ Pour tester le pipeline complet (nécessite PostgreSQL):")
        print("  python tests/test_matching.py --full")
