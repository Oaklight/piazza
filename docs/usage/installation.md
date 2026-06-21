# Installation

## Basic Install

```bash
pip install piazza
```

## With IRC Support

```bash
pip install piazza[irc]
```

## Development Install

```bash
git clone https://github.com/Oaklight/piazza.git
cd piazza
pip install -e ".[dev,test]"
```

## Requirements

- Python 3.10+
- No runtime dependencies (stdlib only)
- Optional: `irc>=20.0` for IRC frontend
