PREFIX ?= /usr/local

SRCS := $(shell find src/ -type f -name '*.py')

all: genpack

genpack: $(SRCS)
	find src -type d -name '__pycache__' -exec rm -r {} +
	python -m zipapp src -p '/usr/bin/python3' -c -o $@

install: all
	install -Dm755 genpack $(DESTDIR)$(PREFIX)/bin/genpack

clean:
	rm -f genpack 
	find src -type d -name '__pycache__' -exec rm -r {} +
