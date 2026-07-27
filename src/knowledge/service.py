from src.knowledge import brain
from src.conversations import history


def export_local_data() -> dict[str, object]:
    return {"exported_at": brain.utc_now().isoformat(), "messages": history.get(), **brain.export_brain_data()}


def delete_local_data() -> None:
    brain.delete_all_brain_data()
    with history.database_connection() as connection:
        connection.execute("DELETE FROM messages")
