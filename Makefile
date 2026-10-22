PYTHON ?= .venv/bin/python
BUNDLE ?=
REPORT_DIR ?= artifacts
RUN_ADDRESS ?= 127.0.0.1:8000
LOCAL = DJANGO_SETTINGS_MODULE=config.settings.local $(PYTHON) manage.py
TEST = DJANGO_SETTINGS_MODULE=config.settings.test $(PYTHON) manage.py

.PHONY: help check test test-import verify run seed schema schema-check static build require-bundle import-dry import-apply

help:
	@printf '%s\n' 'check | test | verify | run | seed | schema | schema-check | static | build' 'test-import BUNDLE=/absolute/path' 'import-dry BUNDLE=/absolute/path [REPORT_DIR=artifacts]' 'import-apply BUNDLE=/absolute/path [REPORT_DIR=artifacts] (LOCAL database only)'

check:
	$(LOCAL) check
	$(LOCAL) makemigrations --check --dry-run

test:
	$(TEST) test -v 1

require-bundle:
	@test -n "$(BUNDLE)" && test -d "$(BUNDLE)" || { printf '%s\n' 'BUNDLE must name an existing export directory.' >&2; exit 2; }

test-import: require-bundle
	DEZOLENT_IMPORT_BUNDLE="$(BUNDLE)" $(TEST) test artists.tests.test_dezolent_import -v 1

verify: check test schema-check

run:
	$(LOCAL) runserver "$(RUN_ADDRESS)"

seed:
	$(LOCAL) seed_artists
	$(LOCAL) seed_references

schema:
	DJANGO_SETTINGS_MODULE=config.settings.local $(PYTHON) scripts/generate_schema_docs.py

schema-check:
	DJANGO_SETTINGS_MODULE=config.settings.local $(PYTHON) scripts/generate_schema_docs.py --check

static:
	DJANGO_SETTINGS_MODULE=config.settings.build $(PYTHON) manage.py collectstatic --noinput

build:
	docker build -t bounced-artists:test .

import-dry: require-bundle
	@mkdir -p "$(REPORT_DIR)"
	@$(LOCAL) import_dezolent --bundle "$(BUNDLE)" --no-color > "$(REPORT_DIR)/dezolent-dry-run.json"
	@$(PYTHON) scripts/import_summary.py "$(REPORT_DIR)/dezolent-dry-run.json"

import-apply: require-bundle
	@mkdir -p "$(REPORT_DIR)"
	@$(LOCAL) import_dezolent --bundle "$(BUNDLE)" --apply --no-color > "$(REPORT_DIR)/dezolent-apply.json"
	@$(PYTHON) scripts/import_summary.py "$(REPORT_DIR)/dezolent-apply.json"
