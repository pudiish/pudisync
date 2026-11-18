# Everything runs in the project's own environment; `make venv` builds it.
PY ?= .venv/bin/python
# python.org's framework build: it runs as Python.app, the identity macOS ties
# Screen Recording permission to. uv's own downloads are bare binaries that
# lose capture access when launched by another capturing process.
BASE_PYTHON ?= /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
# Optional features to install too, e.g. `make venv EXTRAS="flash camera"`.
EXTRAS ?=

.PHONY: venv lock run test sim app icons clean

# Exactly what uv.lock pins, hashes checked; packages the lock doesn't list are removed.
venv:
	uv venv --allow-existing --python $(BASE_PYTHON) .venv
	uv sync --frozen --python $(BASE_PYTHON) $(foreach extra,$(EXTRAS),--extra $(extra))

# After changing pyproject.toml: re-resolve and rewrite uv.lock.
lock:
	uv lock --python $(BASE_PYTHON)

run:
	$(PY) hub.py

# A throwaway environment from the same lock with every extra, so the camera
# and flashing code is tested while .venv stays lean.
test:
	uv run --frozen --isolated --all-extras --group test --python $(BASE_PYTHON) python tests/test_hub.py

# Sandbox hub against a fake WLED board; see README "Testing".
sim:
	$(PY) tools/fake_wled.py --quiet & \
	$(PY) hub.py --config /tmp/sim-config.json --port 8790 --no-bonjour & \
	sleep 3; $(PY) tools/hardware_sim.py --hub http://127.0.0.1:8790; \
	kill %1 %2

app:
	cd macapp && ./build.sh

# ambilight/web/icon.svg is the one logo. After editing it, regenerate the
# iPhone Home Screen PNG (iOS ignores SVG); `make app` renders the Mac icon itself.
icons:
	swift macapp/Icon.swift ambilight/web/icon.svg ambilight/web/apple-touch-icon.png 180

clean:
	find . -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} +
