.PHONY: test test-fast test-integration test-regression test-coverage lint lint-fix deploy import-sirene import-opco

## Lance tous les tests
test:
	pytest tests/ -v --tb=short

## Tests rapides uniquement (pas d'API externe ni de DB)
test-fast:
	pytest tests/ -v --tb=short -m "not slow and not integration and not regression"

## Tests d'intégration (API + DB)
test-integration:
	pytest tests/ -v --tb=short -m integration

## Tests de non-régression du pipeline complet
test-regression:
	pytest tests/test_pipeline.py -v --tb=short -m regression

## Rapport de couverture
test-coverage:
	pytest tests/ --cov=siret_matcher --cov-report=term-missing -m "not slow"

## Lint
lint:
	ruff check siret_matcher/ tests/

## Lint avec correction automatique
lint-fix:
	ruff check --fix siret_matcher/ tests/

## Déploiement
deploy:
	./scripts/deploy.sh

## Import base SIRENE
import-sirene:
	python scripts/import_sirene.py

## Import table OPCO
import-opco:
	python scripts/import_opco.py
