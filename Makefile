PY ?= python3

.PHONY: run test sim app clean

run:
	$(PY) hub.py

test:
	$(PY) tests/test_hub.py

# Sandbox hub against a fake WLED board; see README "Testing".
sim:
	$(PY) tools/fake_wled.py --quiet & \
	$(PY) hub.py --config /tmp/sim-config.json --port 8790 --no-bonjour & \
	sleep 3; $(PY) tools/hardware_sim.py --hub http://127.0.0.1:8790; \
	kill %1 %2

app:
	cd macapp && ./build.sh

clean:
	find . -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} +
