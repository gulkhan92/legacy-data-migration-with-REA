.PHONY: datasets datasets-quick datasets-verify

PYTHON ?= python

# Rebuild every dataset fixture from scratch. Downloads are cached and checksum verified.
datasets:
	$(PYTHON) datasets/scripts/build_all.py --clean

# Rebuild reusing the generated Synthea output.
datasets-quick:
	$(PYTHON) datasets/scripts/build_all.py

# Check that the committed fixtures match datasets/checksums.json.
datasets-verify:
	$(PYTHON) datasets/scripts/verify_fixtures.py
