from src.knowledge import brain_store
from src.conversations import history


def export_user_data() -> dict[str, object]:
    return {"exported_at": brain_store.utc_now().isoformat(), "messages": history.get(), **brain_store.export_brain_data()}


def delete_user_data() -> None:
    brain_store.delete_all_brain_data()
    with history.database_connection() as connection:
        connection.execute("DELETE FROM messages")
