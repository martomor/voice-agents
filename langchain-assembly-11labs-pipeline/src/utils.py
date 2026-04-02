"""
Utility functions for the voice agent pipeline.

Reference: https://github.com/langchain-ai/voice-sandwich-demo
"""

import asyncio
from typing import AsyncIterator, TypeVar

T = TypeVar("T")


async def merge_async_iters(*aiters: AsyncIterator[T]) -> AsyncIterator[T]:
    """
    Merge multiple async iterators into a single async iterator.

    Yields items from all iterators as they become available, using a shared
    queue and one producer task per iterator. Completes only after all
    input iterators are exhausted.

    Used by the TTS stage to concurrently consume upstream pipeline events
    and incoming audio chunks from ElevenLabs at the same time.
    """
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    async def producer(aiter: AsyncIterator) -> None:
        async for item in aiter:
            await queue.put(item)
        await queue.put(sentinel)

    async with asyncio.TaskGroup() as tg:
        for aiter in aiters:
            tg.create_task(producer(aiter))

        finished = 0
        while finished < len(aiters):
            item = await queue.get()
            if item is sentinel:
                finished += 1
            else:
                yield item
