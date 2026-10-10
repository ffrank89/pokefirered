# This controls the tools in the `tools` folder.
# Can be invoked through the `Makefile` or standalone.
#
# The tools are Python scripts, so there is nothing to compile; `make tools`
# only checks that a suitable Python interpreter is available.

MAKEFLAGS += --no-print-directory

# Python interpreter used to run the tools (override with `make PYTHON=...`).
PYTHON ?= python3

TOOLS_DIR := tools
TOOL_NAMES := bin2c gbafix gbagfx jsonproc mapjson mid2agb preproc ramscrgen rsfont scaninc wav2agb

TOOLDIRS := $(TOOL_NAMES:%=$(TOOLS_DIR)/%)

# Tool checking doesnt require a pokefirered dependency scan.
RULES_NO_SCAN += tools check-tools clean-tools
.PHONY: $(RULES_NO_SCAN)

tools: check-tools

check-tools:
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else "error: Python 3.8 or newer is required to run the tools in $(TOOLS_DIR)")'

clean-tools:
	rm -rf $(TOOLDIRS:%=%/__pycache__)
