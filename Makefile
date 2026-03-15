.PHONY: test test-fast test-integration test-regression test-coverage

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
	pytest tests/ --cov=siret_matcher --cov-report=term-missing --tb=short
