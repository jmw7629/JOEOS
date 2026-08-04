"""Canonical conversation service (Phase P3A).

The backend is authoritative for conversation history, message state, and
provider execution. The service only runs inference through the injected JoeOS
AI runtime; it never talks to a provider directly and never fabricates partial
events or provider availability.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, AsyncIterator, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from .repository import SQLiteConversationRepository


class ConversationError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


class ConversationNotFoundError(ConversationError):
    pass


class ConversationForbiddenError(ConversationError):
    pass


class ConversationCapabilityError(ConversationError):
    pass


class ConversationService:
    """Server-authoritative canonical conversations with real AI execution.

    Conversation lifecycle events are published through the shared realtime
    event stream (cursor-resumable) and never include message content, so the
    audit stream stays free of conversation text.
    """

    inference_timeout_seconds = 180

    def __init__(
        self,
        repository: SQLiteConversationRepository,
        *,
        infer: Callable[[List[Dict[str, str]]], Awaitable[object]],
        availability: Callable[[], Optional[Dict[str, object]]],
        now_provider: Callable[[], int],
        uuid_provider: Callable[[], UUID] = uuid4,
        stream_infer: Optional[Callable[[List[Dict[str, str]]], AsyncIterator[object]]] = None,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self._repository = repository
        self._infer = infer
        self._availability = availability
        self._now = now_provider
        self._uuid = uuid_provider
        self._stream_infer = stream_infer
        self._event_sink = event_sink
        self._runs: Dict[UUID, asyncio.Task] = {}
        self._cancelled: Dict[UUID, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    def prepare(self) -> None:
        self._repository.prepare()

    # ------------------------------------------------------------------
    # Conversation lifecycle
    # ------------------------------------------------------------------

    def create_conversation(self, principal: Dict, title: str = "Conversation") -> Dict:
        self._require(principal, "conversation.write")
        record = self._repository.create_conversation(
            conversation_id=self._uuid(),
            user_id=principal["user"]["id"],
            device_id=principal["device_id"],
            organization_id=principal["organization"]["id"],
            workspace_id=principal["workspace"]["id"],
            title=title.strip() or "Conversation",
            now=self._now(),
        )
        self._emit(
            "conversation.created",
            {"conversation_id": str(record.conversation_id), "title": record.title},
        )
        return self._conversation_payload(record, self._repository.list_messages(record.conversation_id))

    def get_conversation(self, principal: Dict, conversation_id: UUID) -> Dict:
        self._require(principal, "conversation.read")
        record = self._repository.get_conversation(conversation_id)
        if record is None:
            raise ConversationNotFoundError(
                404, "conversation_not_found", "The conversation does not exist."
            )
        if record.user_id != principal["user"]["id"] or record.workspace_id != principal["workspace"]["id"]:
            raise ConversationForbiddenError(
                403, "conversation_forbidden", "This conversation is not accessible to this principal."
            )
        return self._conversation_payload(record, self._repository.list_messages(conversation_id))

    def list_conversations(self, principal: Dict, limit: int = 100) -> List[Dict]:
        self._require(principal, "conversation.read")
        records = self._repository.list_conversations(
            principal["user"]["id"], principal["workspace"]["id"], limit=limit
        )
        return [
            self._conversation_payload(record, self._repository.list_messages(record.conversation_id))
            for record in records
        ]

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def submit_message(self, principal: Dict, conversation_id: UUID, content: str) -> Dict:
        self._require(principal, "conversation.write")
        self._require(principal, "conversation.invoke_ai")
        conversation = self.get_conversation(principal, conversation_id)
        text = content.strip()
        if not text:
            raise ConversationError(400, "empty_message", "Message content is required.")
        now = self._now()
        self._repository.append_message(
            message_id=self._uuid(),
            conversation_id=conversation_id,
            role="user",
            content=text,
            status="completed",
            now=now,
        )
        return await self._run_inference(principal, conversation_id)
    async def retry_last_message(self, principal: Dict, conversation_id: UUID) -> Dict:
        """Retries the last user message without corrupting history: a new
        assistant message is produced; prior messages are untouched."""
        self._require(principal, "conversation.write")
        self._require(principal, "conversation.invoke_ai")
        self.get_conversation(principal, conversation_id)
        last = self._repository.last_user_message(conversation_id)
        if last is None:
            raise ConversationError(400, "nothing_to_retry", "There is no message to retry.")
        return await self._run_inference(principal, conversation_id)

    def cancel_run(self, principal: Dict, run_id: UUID) -> bool:
        self._require(principal, "conversation.cancel")
        task = self._runs.get(run_id)
        if task is not None:
            task.cancel()
            return True
        cancel_event = self._cancelled.get(run_id)
        if cancel_event is not None:
            cancel_event.set()
            return True
        return False

    # ------------------------------------------------------------------
    # Streaming (genuine partial events only when the provider streams)
    # ------------------------------------------------------------------

    async def stream_message(
        self, principal: Dict, conversation_id: UUID, content: str
    ) -> AsyncIterator[Dict[str, object]]:
        """Streams one user message and its assistant response as server-sent
        events. Partial deltas are emitted only when the selected provider truly
        supports streaming; otherwise a single completed delta is emitted with
        honest non-streaming semantics."""
        self._require(principal, "conversation.write")
        self._require(principal, "conversation.invoke_ai")
        self.get_conversation(principal, conversation_id)
        text = content.strip()
        if not text:
            raise ConversationError(400, "empty_message", "Message content is required.")
        now = self._now()
        user_message_id = self._uuid()
        self._repository.append_message(
            message_id=user_message_id,
            conversation_id=conversation_id,
            role="user",
            content=text,
            status="completed",
            now=now,
        )
        self._emit(
            "message.appended",
            {"conversation_id": str(conversation_id), "message_id": str(user_message_id), "role": "user"},
        )

        messages = self._repository.list_messages(conversation_id)
        completed = [message for message in messages if message.status == "completed"]
        bounded = completed[-40:]
        wire_messages = [
            {"role": message.role, "content": message.content} for message in bounded
        ]
        assistant_id = self._uuid()
        run_id = assistant_id
        self._repository.append_message(
            message_id=assistant_id,
            conversation_id=conversation_id,
            role="assistant",
            content="",
            status="pending",
            now=now,
            parent_message_id=bounded[-1].message_id if bounded else None,
        )
        cancel_event = asyncio.Event()
        async with self._lock:
            self._cancelled[run_id] = cancel_event
        self._emit(
            "run.started",
            {"conversation_id": str(conversation_id), "run_id": str(run_id), "message_id": str(assistant_id)},
        )

        accumulated = ""
        try:
            availability = self._availability()
            if availability is None or not availability.get("available"):
                reason = "No inference provider is available on the JoeOS backend."
                if availability:
                    reason = str(availability.get("reason") or reason)
                self._repository.complete_message(
                    assistant_id,
                    status="failed",
                    now=self._now(),
                    error_detail=reason,
                )
                yield {"event": "run.failed", "conversation_id": str(conversation_id), "run_id": str(run_id), "reason": reason}
                self._emit("run.failed", {"conversation_id": str(conversation_id), "run_id": str(run_id)})
                return

            streaming = bool(availability.get("streaming"))
            if streaming and self._stream_infer is not None:
                async for delta in self._stream_infer(wire_messages):
                    if cancel_event.is_set():
                        break
                    piece = getattr(delta, "content", "") or ""
                    if not piece:
                        continue
                    accumulated += piece
                    yield {"event": "message.delta", "run_id": str(run_id), "content": piece}
            else:
                result = await self._infer(wire_messages)
                accumulated = getattr(result, "reply", "") or ""
                if accumulated:
                    yield {"event": "message.delta", "run_id": str(run_id), "content": accumulated}

            if cancel_event.is_set():
                self._repository.complete_message(
                    assistant_id,
                    status="cancelled",
                    now=self._now(),
                    content=accumulated,
                    error_detail="cancelled by the operator",
                )
                yield {"event": "run.cancelled", "conversation_id": str(conversation_id), "run_id": str(run_id)}
                self._emit("run.cancelled", {"conversation_id": str(conversation_id), "run_id": str(run_id)})
                return

            self._repository.complete_message(
                assistant_id,
                status="completed",
                now=self._now(),
                content=accumulated,
                provider=str(availability.get("provider_id") or "lemonade"),
                model=str(availability.get("model") or ""),
            )
            yield {
                "event": "run.completed",
                "conversation_id": str(conversation_id),
                "run_id": str(run_id),
                "message_id": str(assistant_id),
                "content": accumulated,
            }
            self._emit("run.completed", {"conversation_id": str(conversation_id), "run_id": str(run_id)})
        except asyncio.CancelledError:
            self._repository.complete_message(
                assistant_id,
                status="cancelled",
                now=self._now(),
                content=accumulated,
                error_detail="cancelled by the operator",
            )
            yield {"event": "run.cancelled", "conversation_id": str(conversation_id), "run_id": str(run_id)}
            self._emit("run.cancelled", {"conversation_id": str(conversation_id), "run_id": str(run_id)})
            raise
        except Exception as error:  # noqa: BLE001
            self._repository.complete_message(
                assistant_id,
                status="failed",
                now=self._now(),
                content=accumulated,
                error_detail=str(error)[:240],
            )
            yield {"event": "run.failed", "conversation_id": str(conversation_id), "run_id": str(run_id), "reason": str(error)[:240]}
            self._emit("run.failed", {"conversation_id": str(conversation_id), "run_id": str(run_id)})
        finally:
            async with self._lock:
                self._cancelled.pop(run_id, None)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    async def _run_inference(self, principal: Dict, conversation_id: UUID) -> Dict:
        messages = self._repository.list_messages(conversation_id)
        completed = [message for message in messages if message.status == "completed"]
        bounded = completed[-40:]
        wire_messages = [
            {"role": message.role, "content": message.content} for message in bounded
        ]
        now = self._now()
        assistant_id = self._uuid()
        run_id = assistant_id
        self._repository.append_message(
            message_id=assistant_id,
            conversation_id=conversation_id,
            role="assistant",
            content="",
            status="pending",
            now=now,
            parent_message_id=bounded[-1].message_id if bounded else None,
        )
        self._emit("run.started", {"conversation_id": str(conversation_id), "run_id": str(run_id)})
        task = asyncio.create_task(self._execute(assistant_id, conversation_id, wire_messages))
        async with self._lock:
            self._runs[run_id] = task
        try:
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self.inference_timeout_seconds,
                )
            except asyncio.TimeoutError:
                task.cancel()
                with _suppress(asyncio.CancelledError):
                    await task
                self._repository.complete_message(
                    assistant_id,
                    status="cancelled",
                    now=self._now(),
                    error_detail="inference timed out",
                )
            except asyncio.CancelledError:
                # The run was cancelled server-side; `_execute` already recorded
                # the message as cancelled. The authoritative message state wins.
                pass
        finally:
            async with self._lock:
                self._runs.pop(run_id, None)
        return self.get_conversation(principal, conversation_id)

    async def _execute(
        self,
        assistant_id: UUID,
        conversation_id: UUID,
        wire_messages: List[Dict[str, str]],
    ) -> None:
        try:
            availability = self._availability()
            if availability is None or not availability.get("available"):
                reason = "No inference provider is available on the JoeOS backend."
                if availability:
                    reason = str(availability.get("reason") or reason)
                self._repository.complete_message(
                    assistant_id,
                    status="failed",
                    now=self._now(),
                    error_detail=reason,
                )
                self._emit("run.failed", {"conversation_id": str(conversation_id), "run_id": str(assistant_id)})
                return
            result = await self._infer(wire_messages)
            self._repository.complete_message(
                assistant_id,
                status="completed",
                now=self._now(),
                content=getattr(result, "reply", ""),
                provider=getattr(result, "provider", None),
                model=getattr(result, "model", None),
                tokens_used=getattr(result, "tokens_used", None),
            )
            self._emit("run.completed", {"conversation_id": str(conversation_id), "run_id": str(assistant_id)})
        except asyncio.CancelledError:
            self._repository.complete_message(
                assistant_id,
                status="cancelled",
                now=self._now(),
                error_detail="cancelled by the operator",
            )
            self._emit("run.cancelled", {"conversation_id": str(conversation_id), "run_id": str(assistant_id)})
            raise
        except Exception as error:  # noqa: BLE001
            self._repository.complete_message(
                assistant_id,
                status="failed",
                now=self._now(),
                error_detail=str(error)[:240],
            )
            self._emit("run.failed", {"conversation_id": str(conversation_id), "run_id": str(assistant_id)})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def streaming_supported(self, principal: Dict) -> bool:
        availability = self._availability()
        return bool(availability and availability.get("streaming"))

    def _emit(self, event_type: str, payload: Dict[str, object]) -> None:
        if self._event_sink is None:
            return
        detail = json.dumps(
            {"event": event_type, "data": payload}, sort_keys=True, separators=(",", ":")
        )
        self._event_sink("info", "conversations", detail[:480])

    @staticmethod
    def _require(principal: Dict, capability: str) -> None:
        capabilities = principal.get("capabilities") or []
        if capability not in capabilities:
            raise ConversationCapabilityError(
                403,
                "capability_denied",
                "This principal is not granted the %s capability." % capability,
            )

    @staticmethod
    def _conversation_payload(record: object, messages: List[object]) -> Dict:
        return {
            "conversation_id": getattr(record, "conversation_id"),
            "title": getattr(record, "title"),
            "status": getattr(record, "status"),
            "created_at": getattr(record, "created_at"),
            "updated_at": getattr(record, "updated_at"),
            "revision": getattr(record, "revision"),
            "messages": [
                {
                    "message_id": message.message_id,
                    "role": message.role,
                    "content": message.content,
                    "status": message.status,
                    "provider": message.provider,
                    "model": message.model,
                    "tokens_used": message.tokens_used,
                    "created_at": message.created_at,
                    "completed_at": message.completed_at,
                    "error_detail": message.error_detail,
                }
                for message in messages
            ],
        }


class _suppress:
    def __init__(self, *exceptions) -> None:
        self._exceptions = exceptions

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, self._exceptions)
