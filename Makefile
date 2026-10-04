.PHONY: test install-dev package-check evidence

install-dev:
	python -m pip install -e '.[dev]'

test:
	pytest

package-check:
	python -c "from importlib.resources import files; assert (files('dragonfruitme') / 'SKILL.md').is_file()"

evidence:
	python scripts/scenario_matrix.py
