from typing import Dict, Optional

from app.db.table_binding import TableBinding


class AppState:
    DB_CONFIG: Dict[str, dict] = {}
    API_KEYS: Dict[str, dict] = {}
    TABLES_CONFIG: Dict[str, TableBinding] = {}
    BOOTSTRAPPED: bool = False


state = AppState()
