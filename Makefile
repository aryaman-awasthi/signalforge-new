# SignalForge build interface. Targets: run, rtm, report, evidence, clean-check.
# Recipes are thin delegations so every target is also runnable by hand in bash.

SHELL := bash
PY := $(shell [ -x .venv/Scripts/python.exe ] && echo .venv/Scripts/python.exe || { [ -x .venv/bin/python ] && echo .venv/bin/python || echo python; })

.PHONY: run rtm report erd openapi nfr evidence llm-smoke clean-check help

help:
	@echo "SignalForge targets:"
	@echo "  make run          boot api + web (run.sh)"
	@echo "  make rtm          generate docs/20-RTM.md from specs + test results"
	@echo "  make report       generate docs/21-test-report.md"
	@echo "  make erd          generate docs/10-data-design.md from the models"
	@echo "  make nfr          generate docs/16 + docs/17 from a recorded nfr run"
	@echo "  make openapi      export docs/11-openapi.json + the docs/11 route catalogue"
	@echo "  make evidence     pull runtime evidence for one requirement: make evidence REQ=REQ-021"
	@echo "  make llm-smoke    check the GenAI Lab gateway (opt-in; needs GENAILAB_API_KEY)"
	@echo "  make clean-check  enforce the app-agnostic core rule (scripts/check_clean.sh)"

run:
	@bash run.sh

rtm:
	@if [ -f scripts/gen_rtm.py ]; then $(PY) scripts/gen_rtm.py; \
	else echo "make rtm: scripts/gen_rtm.py not implemented yet (arrives S0-T03)"; exit 1; fi

report:
	@if [ -f scripts/gen_test_report.py ]; then $(PY) scripts/gen_test_report.py; \
	else echo "make report: scripts/gen_test_report.py not implemented yet"; exit 1; fi

erd:
	@if [ -f scripts/gen_erd.py ]; then $(PY) scripts/gen_erd.py; \
	else echo "make erd: scripts/gen_erd.py not implemented yet"; exit 1; fi

openapi:
	@if [ -f scripts/gen_openapi.py ]; then $(PY) scripts/gen_openapi.py; \
	else echo "make openapi: scripts/gen_openapi.py not implemented yet"; exit 1; fi

evidence:
	@if [ -f scripts/evidence.py ]; then $(PY) scripts/evidence.py $(REQ); \
	else echo "make evidence: scripts/evidence.py not implemented yet"; exit 1; fi

# The only target that talks to a live model. Never part of `make test`.
# Loads .env the same way run.sh does, so the smoke test and the running
# platform are asking the same gateway with the same credential.
llm-smoke:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; $(PY) scripts/llm_smoke.py $(ARGS)

clean-check:
	@bash scripts/check_clean.sh
