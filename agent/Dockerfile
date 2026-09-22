FROM python:3.14-slim AS base
RUN pip install --no-cache-dir poetry
WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
  && poetry install --no-interaction --no-ansi --without dev
COPY autosentry_agent/ autosentry_agent/
COPY schema/ schema/
COPY alembic/ alembic/
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 autosentry
USER autosentry
EXPOSE 8000
CMD ["uvicorn", "autosentry_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
