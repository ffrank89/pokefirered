# Game code written in Python.
#
# A file src/foo.py written in the gba dialect of Python (see python/gba/__init__.py)
# is translated into src/foo.c by tools/py2c/py2c.py, and that C file is then
# compiled like any other. Edit the .py file; the .c file is regenerated.

PY2C := $(PYTHON) $(TOOLS_DIR)/py2c/py2c.py
PY2C_DEPS := $(TOOLS_DIR)/py2c/py2c.py $(wildcard python/gba/*.py)

PY_GAME_SRCS := $(wildcard $(C_SUBDIR)/*.py $(C_SUBDIR)/*/*.py)
PY_GAME_C := $(PY_GAME_SRCS:.py=.c)

AUTO_GEN_TARGETS += $(PY_GAME_C)

$(PY_GAME_C): %.c: %.py $(PY2C_DEPS)
	$(PY2C) $< $@
