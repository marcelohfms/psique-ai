import os
import pytest

# Env mínimo para importar os módulos do dashboard sem inicializar nada real.
os.environ.setdefault("SUPABASE_URL", "http://fake.local")
os.environ.setdefault("SUPABASE_KEY", "fake-key")
os.environ.setdefault("ATTENDANT_PANEL_TOKEN", "test-token")


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Imita o query-builder do postgrest-py para os usos do painel."""
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters = []  # list[tuple[kind, col, val]]
        self._order_col = None
        self._order_desc = False
        self._limit = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, values):
        self._filters.append(("in", col, values))
        return self

    def order(self, col, desc=False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        for kind, col, val in self._filters:
            if kind == "eq" and row.get(col) != val:
                return False
            if kind == "in" and row.get(col) not in val:
                return False
        return True

    async def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "select":
            matched = [r for r in rows if self._matches(r)]
            if self._order_col is not None:
                matched.sort(key=lambda r: r.get(self._order_col), reverse=self._order_desc)
            if self._limit is not None:
                matched = matched[: self._limit]
            return FakeResult(matched)
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                rows.append(dict(p))
            return FakeResult([dict(p) for p in payload])
        if self._op == "update":
            changed = []
            for r in rows:
                if self._matches(r):
                    r.update(self._payload)
                    changed.append(dict(r))
            return FakeResult(changed)
        if self._op == "delete":
            kept, removed = [], []
            for r in rows:
                (removed if self._matches(r) else kept).append(r)
            self._store[self._table] = kept
            return FakeResult(removed)
        return FakeResult([])


class FakeClient:
    def __init__(self, store=None):
        self.store = store if store is not None else {}

    def from_(self, table):
        return FakeQuery(self.store, table)


@pytest.fixture
def fake_client():
    return FakeClient()
