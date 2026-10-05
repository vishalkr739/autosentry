FROM python:3.14-slim AS base
RUN pip install --no-cache-dir poetry
WORKDIR /app
COPY pyproject.toml poetry.lock ./
COPY autosentry_agent/ autosentry_agent/
COPY schema/ schema/
COPY queries/ queries/
COPY alembic/ alembic/
COPY alembic.ini ./
RUN poetry config virtualenvs.create false \
  && poetry install --no-interaction --no-ansi --without dev
RUN useradd --create-home --uid 10001 autosentry
USER autosentry
EXPOSE 8000
CMD ["uvicorn", "autosentry_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
