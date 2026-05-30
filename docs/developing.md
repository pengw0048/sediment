# Developing

Run the local gate before committing:

```bash
ruff check .
ruff format --check .
mypy pke/
pytest
```
