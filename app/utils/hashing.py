import datetime
import hashlib
import json


def compute_hash(data: dict) -> str:
    """Вычисление hash от data_fields (для дедупликации)"""
    # Сериализуем dict рекурсивно
    def serialize(obj):
        if isinstance(obj, dict):
            return {k: serialize(v) for k, v in sorted(obj.items())}
        elif isinstance(obj, list):
            return [serialize(item) for item in obj]
        elif isinstance(obj, datetime.datetime):
            return obj.isoformat()
        elif isinstance(obj, datetime.date):
            return obj.isoformat()
        elif isinstance(obj, datetime.time):
            return obj.isoformat()
        return obj

    normalized = serialize(data)
    json_str = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(json_str.encode()).hexdigest()
