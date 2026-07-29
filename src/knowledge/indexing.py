import asyncio
import logging

from src.core.errors import try_async
from src.knowledge import brain_store
from src.knowledge.public_link_reader import read_public_link

logger = logging.getLogger(__name__)


class SourceIndexer:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._runner: asyncio.Task[None] | None = None

    def enqueue(self, item_id: int) -> None:
        self._queue.put_nowait(item_id)

    async def start(self) -> None:
        if self._runner is not None:
            return
        pending_item_ids = await asyncio.to_thread(brain_store.list_pending_source_ids)
        for item_id in pending_item_ids:
            self.enqueue(item_id)
        self._runner = asyncio.create_task(self._run(), name="source-indexer")

    async def stop(self) -> None:
        if self._runner is None:
            return
        self._runner.cancel()
        await asyncio.gather(self._runner, return_exceptions=True)
        self._runner = None

    async def retry(self, item_id: int) -> bool:
        reset = await asyncio.to_thread(brain_store.retry_source, item_id)
        if reset:
            self.enqueue(item_id)
        return reset

    async def _run(self) -> None:
        while True:
            item_id = await self._queue.get()
            await try_async(
                lambda: self._enrich(item_id),
                handle_error=lambda error: self._handle_failure(item_id, error),
            )
            self._queue.task_done()

    async def _enrich(self, item_id: int) -> None:
        item = await asyncio.to_thread(brain_store.get_item, item_id)
        if item is None or item.source_url is None or item.source_status != "pending":
            return
        source = await read_public_link(item.source_url)
        if source.status != "analyzed":
            await asyncio.to_thread(brain_store.mark_source_unavailable, item_id)
            return
        summary = source.text[:500] if source.text else None
        await asyncio.to_thread(brain_store.update_source_metadata, item_id, source.title, summary)

    async def _handle_failure(self, item_id: int, error: BaseException) -> None:
        logger.exception("Unable to enrich saved link %s", item_id, exc_info=error)
        await asyncio.to_thread(brain_store.mark_source_unavailable, item_id)


source_indexer = SourceIndexer()


def enqueue_source_item(item_id: int) -> None:
    source_indexer.enqueue(item_id)
