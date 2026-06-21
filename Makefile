.PHONY: build serve clean

build:
	mkdocs build

serve:
	mkdocs serve

clean:
	rm -rf site/
