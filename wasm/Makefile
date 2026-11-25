# Sayso -> WebAssembly.
#
# The source list is Sayso's own `LIB_SRC` plus Scalpel's tokeniser, compiled by
# em++ instead of g++. Nothing is forked and nothing is patched: if this list
# drifts from Sayso's Makefile the parity test fails, which is the point of
# having one.
#
#   make            -> dist/sayso.js + dist/sayso.wasm
#   make clean
#
# SAYSO and SCALPEL default to sibling checkouts and can be pointed elsewhere:
#   make SAYSO=/path/to/sayso SCALPEL=/path/to/Scalpel

EMXX ?= em++

SAYSO   ?= ../../sayso
SCALPEL ?= ../../Scalpel

# -O3 because this runs once per keystroke-batch in a browser and the module is
# small enough that compile time is irrelevant. -fno-exceptions is deliberately
# absent: the grammars do not throw, but std::string does on allocation failure
# and silently aborting inside a tab is worse than an exception.
CXXFLAGS = -std=c++17 -O3 -I$(SAYSO) -I$(SCALPEL)

ENGINE_SRC  = wfst.cpp symbols.cpp
GRAMMAR_SRC = addresses.cpp names.cpp states.cpp phones.cpp proper.cpp \
              numbers.cpp money.cpp dates.cpp decades.cpp abbrev.cpp letters.cpp \
              ranges.cpp telephone.cpp decimals.cpp fractions.cpp times.cpp \
              measures.cpp
LIB_SRC     = $(ENGINE_SRC) $(GRAMMAR_SRC) text.cpp classify.cpp normalizer.cpp \
              report.cpp

SRC = sayso_wasm.cpp \
      $(addprefix $(SAYSO)/,$(LIB_SRC)) \
      $(SCALPEL)/tokenizer.cpp $(SCALPEL)/char_classes.cpp

# MODULARIZE + EXPORT_ES6 so the page can `import createSayso from './sayso.js'`
# and instantiate it when the user first types, rather than at page load.
#
# ALLOW_MEMORY_GROWTH because the grammars are built lazily on the first call
# and the composed lattices for a long sentence are not bounded by anything we
# control. INITIAL_MEMORY is sized so the flagship sentence never grows at all.
#
# SINGLE_FILE is off: a separate .wasm is cacheable and compiles while it
# downloads, where a base64 blob inside the .js is neither.
EMFLAGS = \
  -s MODULARIZE=1 \
  -s EXPORT_ES6=1 \
  -s EXPORT_NAME=createSayso \
  -s ENVIRONMENT=web,worker,node \
  -s ALLOW_MEMORY_GROWTH=1 \
  -s INITIAL_MEMORY=33554432 \
  -s STACK_SIZE=5242880 \
  -s EXPORTED_FUNCTIONS='["_sayso_stage_result","_sayso_normalize","_malloc","_free"]' \
  -s EXPORTED_RUNTIME_METHODS='["ccall","cwrap","UTF8ToString","stringToNewUTF8"]' \
  -s FILESYSTEM=0 \
  --closure 0

.PHONY: all clean size

all: dist/sayso.js

dist/sayso.js: $(SRC) $(SAYSO)/*.h $(SCALPEL)/*.h
	@mkdir -p dist
	$(EMXX) $(CXXFLAGS) $(EMFLAGS) -o $@ $(SRC)

size: dist/sayso.js
	@ls -l dist/sayso.js dist/sayso.wasm | awk '{printf "%-24s %8.1f KB\n", $$9, $$5/1024}'
	@gzip -c dist/sayso.wasm | wc -c | awk '{printf "%-24s %8.1f KB\n", "sayso.wasm (gzipped)", $$1/1024}'
	@gzip -c dist/sayso.js   | wc -c | awk '{printf "%-24s %8.1f KB\n", "sayso.js (gzipped)", $$1/1024}'

clean:
	rm -rf dist
