"""Read raw rows separately from validation; never fill, delete, or repair."""
import csv
import json
from datetime import date
from pathlib import Path
from .schemas import Price, Instrument, CorporateAction, Portfolio, Provenance

class DatasetError(ValueError):
    pass

def read_rows(path: Path, columns: list[str]) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != columns:
            raise DatasetError(f"{path.name}: expected columns {columns}")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise DatasetError(f"{path.name}: malformed CSV row")
    return rows

def load_dataset(root: Path) -> dict:
    instruments = [Instrument.model_validate(r) for r in read_rows(root/'instruments.csv', ['instrument','sector','currency'])]
    names = {i.instrument for i in instruments}
    if not names or len(names) != len(instruments):
        raise DatasetError('empty or duplicate instrument registry')
    provenance = Provenance.model_validate_json((root/'provenance.json').read_text())
    portfolio = Portfolio.model_validate_json((root/'portfolio.json').read_text())
    if any(i.currency != provenance.currency for i in instruments) or portfolio.base_currency != provenance.currency:
        raise DatasetError('only one base currency is supported')
    if not {w.instrument for w in portfolio.weights} <= names:
        raise DatasetError('unknown portfolio instrument')
    calendar = [date.fromisoformat(r['date']) for r in read_rows(root/'calendar.csv', ['date'])]
    if not calendar or calendar != sorted(set(calendar)):
        raise DatasetError('calendar must be nonempty, unique and sorted')
    if calendar[0] != provenance.start_date or calendar[-1] != provenance.end_date:
        raise DatasetError('calendar bounds disagree with provenance')
    prices = [Price.model_validate(r) for r in read_rows(root/'prices.csv', ['date','instrument','adjusted_close'])]
    keys = [(r.date, r.instrument) for r in prices]
    if len(set(keys)) != len(keys):
        raise DatasetError('duplicate date per instrument')
    if any(r.instrument not in names for r in prices):
        raise DatasetError('unknown price instrument')
    if any(r.date not in set(calendar) for r in prices):
        raise DatasetError('price date outside supplied calendar')
    missing = sorted(set((d,n) for d in calendar for n in names) - set(keys))
    if missing:
        raise DatasetError(f'missing expected observations: {missing[:5]} (total {len(missing)})')
    actions = [CorporateAction.model_validate(r) for r in read_rows(root/'corporate_actions.csv', ['date','instrument','action','value','evidence'])]
    if any(a.instrument not in names or a.date not in calendar for a in actions):
        raise DatasetError('corporate action outside instrument registry or calendar')
    return dict(prices=prices, instruments=instruments, portfolio=portfolio, provenance=provenance, calendar=calendar, corporate_actions=actions)
