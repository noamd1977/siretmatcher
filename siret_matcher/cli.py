"""CLI pour SIRET Matcher."""
import asyncio
import logging
import sys
import click
import pandas as pd
from siret_matcher.models import Prospect
from siret_matcher.matcher import match_batch, prospects_to_dicts


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Réduire le bruit des libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)


def load_prospects(filepath: str) -> list[Prospect]:
    """Charger des prospects depuis un CSV ou XLSX."""
    if filepath.endswith(".xlsx") or filepath.endswith(".xls"):
        df = pd.read_excel(filepath, dtype=str)
    else:
        df = pd.read_csv(filepath, dtype=str)

    df = df.fillna("")

    # Mapping flexible des colonnes
    col_map = {}
    for col in df.columns:
        cl = col.lower().strip()
        if cl in ("nom", "name", "raison_sociale", "entreprise"):
            col_map["nom"] = col
        elif cl in ("adresse", "address", "adresse_complete"):
            col_map["adresse"] = col
        elif cl in ("code_postal", "cp", "postal_code", "zip"):
            col_map["code_postal"] = col
        elif cl in ("ville", "city", "commune"):
            col_map["ville"] = col
        elif cl in ("departement", "dept", "department"):
            col_map["departement"] = col
        elif cl in ("telephone", "tel", "phone"):
            col_map["telephone"] = col
        elif cl in ("site_web", "website", "url", "site"):
            col_map["site_web"] = col
        elif cl in ("email", "mail", "e-mail"):
            col_map["email"] = col
        elif cl in ("secteur_recherche", "secteur", "type", "category"):
            col_map["secteur_recherche"] = col
        elif cl in ("place_id",):
            col_map["place_id"] = col
        elif cl in ("rating", "note"):
            col_map["rating"] = col
        elif cl in ("avis", "reviews", "nb_avis"):
            col_map["avis"] = col

    if "nom" not in col_map:
        raise click.ClickException(f"Colonne 'nom' introuvable. Colonnes disponibles: {list(df.columns)}")

    prospects = []
    for _, row in df.iterrows():
        p = Prospect(
            nom=row.get(col_map.get("nom", ""), ""),
            adresse=row.get(col_map.get("adresse", ""), ""),
            code_postal=str(row.get(col_map.get("code_postal", ""), "")).replace(".0", ""),
            ville=row.get(col_map.get("ville", ""), ""),
            departement=row.get(col_map.get("departement", ""), ""),
            telephone=row.get(col_map.get("telephone", ""), ""),
            site_web=row.get(col_map.get("site_web", ""), ""),
            email=row.get(col_map.get("email", ""), ""),
            secteur_recherche=row.get(col_map.get("secteur_recherche", ""), ""),
            place_id=row.get(col_map.get("place_id", ""), ""),
            rating=row.get(col_map.get("rating", ""), ""),
            avis=row.get(col_map.get("avis", ""), ""),
        )
        if p.nom:
            prospects.append(p)

    return prospects


@click.command()
@click.argument("input_file", required=False)
@click.option("--output", "-o", default=None, help="Fichier de sortie (CSV ou XLSX)")
@click.option("--gsheet", default=None, help="ID du Google Spreadsheet")
@click.option("--sheet", default="Feuille 1", help="Nom de la feuille Google Sheets")
@click.option("--no-db", is_flag=True, help="Désactiver les étapes base locale (3-4)")
@click.option("--concurrency", "-c", default=5, help="Nombre de matchs parallèles")
@click.option("--verbose", "-v", is_flag=True, help="Mode verbeux")
@click.option("--limit", "-n", default=0, help="Limiter le nombre de prospects (0 = tous)")
def main(input_file, output, gsheet, sheet, no_db, concurrency, verbose, limit):
    """SIRET Matcher v2.0 — Enrichissement haute performance de prospects Google Maps.
    
    Exemples:
    
        python -m siret_matcher.cli prospects.csv -o enriched.csv
        
        python -m siret_matcher.cli prospects.xlsx -o enriched.xlsx -v
        
        python -m siret_matcher.cli --gsheet "1ABC...xyz" --sheet "Prospects"
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    # ---- Chargement des prospects ----
    if input_file:
        logger.info(f"Chargement: {input_file}")
        prospects = load_prospects(input_file)
    elif gsheet:
        logger.info(f"Chargement Google Sheet: {gsheet}")
        prospects = _load_from_gsheet(gsheet, sheet)
    else:
        raise click.ClickException("Spécifiez un fichier CSV/XLSX ou --gsheet ID")

    if limit > 0:
        prospects = prospects[:limit]

    logger.info(f"{len(prospects)} prospects à matcher")

    # ---- Matching ----
    results = asyncio.run(match_batch(prospects, use_db=not no_db, concurrency=concurrency))

    # ---- Stats ----
    found = sum(1 for p in results if p.result and p.result.siret)
    total = len(results)
    logger.info(f"\n{'='*60}")
    logger.info(f"RÉSULTATS: {found}/{total} matchés ({found/total*100:.0f}%)")

    # Stats par méthode
    methods = {}
    for p in results:
        m = p.result.methode if p.result else "ERREUR"
        methods[m] = methods.get(m, 0) + 1
    for m, c in sorted(methods.items(), key=lambda x: -x[1]):
        logger.info(f"  {m}: {c}")
    logger.info(f"{'='*60}")

    # ---- Export ----
    rows = prospects_to_dicts(results)
    df_out = pd.DataFrame(rows)

    if not output:
        ext = input_file.rsplit(".", 1)[-1] if input_file else "csv"
        output = (input_file.rsplit(".", 1)[0] if input_file else "output") + f"_enriched.{ext}"

    if output.endswith(".xlsx"):
        df_out.to_excel(output, index=False)
    else:
        df_out.to_csv(output, index=False)

    logger.info(f"Export: {output}")

    # Si Google Sheets
    if gsheet:
        _export_to_gsheet(df_out, gsheet, sheet + " Enrichi")


def _load_from_gsheet(spreadsheet_id: str, sheet_name: str) -> list[Prospect]:
    """Charger depuis Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        "config/service_account.json",
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(sheet_name)
    df = pd.DataFrame(ws.get_all_records())
    df = df.fillna("").astype(str)

    # Sauvegarder temporairement en CSV pour réutiliser load_prospects
    tmp = "/tmp/gsheet_input.csv"
    df.to_csv(tmp, index=False)
    return load_prospects(tmp)


def _export_to_gsheet(df: pd.DataFrame, spreadsheet_id: str, sheet_name: str):
    """Exporter vers Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        "config/service_account.json",
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)

    try:
        ws = sh.worksheet(sheet_name)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_name, rows=len(df) + 1, cols=len(df.columns))

    ws.update([df.columns.tolist()] + df.values.tolist())
    logging.getLogger(__name__).info(f"Google Sheets mis à jour: {sheet_name}")


if __name__ == "__main__":
    main()
