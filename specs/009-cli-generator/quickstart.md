# Quickstart & Validation: CLI & Project Generator

```bash
ferro new shop
cd shop
ferro makemigrations && ferro migrate
FERRO_SUPERUSER_PASSWORD="a-long-secret" ferro createsuperuser --username admin --no-input
ferro shell -c "print(await Product.objects.count())"     # 0
python -m pytest                                          # the generated tests pass
ferro dev                                                 # http://127.0.0.1:8000/ and /admin/
```

Automated: `pytest tests/integration/test_project.py tests/python/test_dotenv.py
tests/python/test_testing.py`.
