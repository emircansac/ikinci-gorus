.PHONY: demo serve health smoke test pipeline-smoke install

VENV := venv/bin/activate
PY := . venv/bin/activate && python

install:
	python3 -m venv venv
	. venv/bin/activate && pip install flask pandas anthropic requests youtube-transcript-api gunicorn pytest

demo:
	$(PY) demo_seed_and_run.py

serve:
	$(PY) app.py

health:
	SKIP_NLI_CHECK=1 $(PY) pipeline/00_healthcheck.py --local-only

health-api:
	SKIP_NLI_CHECK=1 $(PY) pipeline/00_healthcheck.py

smoke: demo
	curl -sf http://localhost:8000/healthz || echo "Sunucu kapalı — 'make serve' ile başlatın"

test:
	$(PY) -m pytest tests/ -q

pipeline-smoke:
	SKIP_NLI_CHECK=1 $(PY) run_pipeline.py --channels data/channels.csv --max-videos 2
