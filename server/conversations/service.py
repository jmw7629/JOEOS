"""Server-authoritative canonical conversations with durable runs (Phase P3A).

The JoeOS backend is authoritative for conversation history, message state, and
run status. Conversation lifecycle events are published through the shared
realtime event stream with a typed envelope (schema version, organization and
workspace scope, principal scope, conversation id, run id, timestamp, trace id)
and cursor-based reconnect. Event payloads never contain credentials or message
content from unrelated conversations.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, AsyncIterator, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from .repository import SQLiteConversationRepository

EVENT_SCHEMA_VERSION = 1


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
    """Server-authoritative canonical conversations with durable runs."""

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

    def recover_after_restart(self) -> int:
        """Interrupts runs left in queued/running/cancellation_requested after a
        restart. User messages are preserved; no assistant response is invented."""
        return self._repository.interrupt_stale_runs(self._now())

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
            principal,
            "conversation.created",
            conversation_id=record.conversation_id,
            data={"title": record.title},
        )
        return self._conversation_payload(record, self._repository.list_messages(record.conversation_id))

    def rename_conversation(self, principal: Dict, conversation_id: UUID, title: str) -> Dict:
        self._require(principal, "conversation.write")
        self.get_conversation(principal, conversation_id)
        trimmed = title.strip()
        if not trimmed or len(trimmed) > 120:
            raise ConversationError(400, "invalid_title", "A title of 1-120 characters is required.")
        self._repository.set_conversation_title(conversation_id, trimmed, self._now())
        self._emit(principal, "conversation.updated", conversation_id=conversation_id, data={"title": trimmed})
        return self.get_conversation(principal, conversation_id)

    def archive_conversation(self, principal: Dict, conversation_id: UUID) -> Dict:
        self._require(principal, "conversation.write")
        self.get_conversation(principal, conversation_id)
        self._repository.set_conversation_status(conversation_id, "archived", self._now())
        self._emit(principal, "conversation.archived", conversation_id=conversation_id)
        return self.get_conversation(principal, conversation_id)

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

    def load_run(self, principal: Dict, run_id: UUID) -> Dict:
        self._require(principal, "conversation.read")
        run = self._repository.get_run(run_id)
        if run is None:
            raise ConversationNotFoundError(404, "run_not_found", "The run does not exist.")
        self.get_conversation(principal, run.conversation_id)
        return self._run_payload(run)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def submit_message(
        self,
        principal: Dict,
        conversation_id: UUID,
        content: str,
        idempotency_key: Optional[UUID] = None,
    ) -> Dict:
        self._require(principal, "conversation.write")
        self._require(principal, "conversation.invoke_ai")
        conversation = self.get_conversation(principal, conversation_id)
        text = content.strip()
        if not text:
            raise ConversationError(400, "empty_message", "Message content is required.")
        user_message_id = self._accept_user_message(
            principal, conversation_id, text, idempotency_key
        )
        return await self._run_inference(principal, conversation_id, user_message_id, parent_run_id=None)

    async def retry_last_message(
        self, principal: Dict, conversation_id: UUID, parent_run_id: Optional[UUID] = None
    ) -> Dict:
        """Retries the last user message without duplicating it. Creates a new
        run related to the original run."""
        self._require(principal, "conversation.write")
        self._require(principal, "conversation.invoke_ai")
        self.get_conversation(principal, conversation_id)
        last = self._repository.last_user_message(conversation_id)
        if last is None:
            raise ConversationError(400, "nothing_to_retry", "There is no message to retry.")
        prior_run = self._repository.get_run(parent_run_id) if parent_run_id else None
        if prior_run is not None and prior_run.conversation_id != conversation_id:
            raise ConversationError(400, "run_mismatch", "The prior run does not belong to this conversation.")
        return await self._run_inference(
            principal, conversation_id, last.message_id, parent_run_id=parent_run_id
        )

    def cancel_run(self, principal: Dict, run_id: UUID) -> bool:
        self._require(principal, "conversation.cancel")
        run = self._repository.get_run(run_id)
        if run is not None and run.status == "queued":
            self._repository.update_run(run_id, status="cancelled", now=self._now())
            self._emit(
                principal,
                "run.cancelled",
                conversation_id=run.conversation_id,
                run_id=run_id,
            )
            return True
        if run is not None and run.status == "running":
            self._repository.update_run(
                run_id, status="cancellation_requested", now=self._now()
            )
            self._emit(
                principal,
                "run.cancellation_requested",
                conversation_id=run.conversation_id,
                run_id=run_id,
            )
        task = self._runs.get(run_id)
        if task is not None:
            task.cancel()
            return True
        cancel_event = self._cancelled.get(run_id)
        if cancel_event is not None:
            cancel_event.set()
            return True
        return run is not None

    # ------------------------------------------------------------------
    # Streaming (genuine partial events only when the provider streams)
    # ------------------------------------------------------------------

    async def stream_message(
        self,
        principal: Dict,
        conversation_id: UUID,
        content: str,
        idempotency_key: Optional[UUID] = None,
    ) -> AsyncIterator[Dict[str, object]]:
        self._require(principal, "conversation.write")
        self._require(principal, "conversation.invoke_ai")
        self.get_conversation(principal, conversation_id)
        text = content.strip()
        if not text:
            raise ConversationError(400, "empty_message", "Message content is required.")
        user_message_id = self._accept_user_message(principal, conversation_id, text, idempotency_key)

        messages = self._repository.list_messages(conversation_id)
        completed = [message for message in messages if message.status == "completed"]
        bounded = completed[-40:]
        wire_messages = [
            {"role": message.role, "content": message.content} for message in bounded
        ]
        assistant_id = self._uuid()
        run_id = self._uuid()
        now = self._now()
        self._repository.append_message(
            message_id=assistant_id,
            conversation_id=conversation_id,
            role="assistant",
            content="",
            status="pending",
            now=now,
            parent_message_id=user_message_id,
        )
        self._repository.create_run(
            run_id=run_id,
            conversation_id=conversation_id,
            message_id=assistant_id,
            status="queued",
            now=now,
        )
        self._emit(
            principal,
            "run.queued",
            conversation_id=conversation_id,
            run_id=run_id,
            data={"message_id": str(assistant_id)},
        )

        cancel_event = asyncio.Event()
        async with self._lock:
            self._cancelled[run_id] = cancel_event
        self._repository.update_run(run_id, status="running", now=self._now())
        self._emit(principal, "run.started", conversation_id=conversation_id, run_id=run_id)

        accumulated = ""
        try:
            availability = self._availability()
            if availability is None or not availability.get("available"):
                reason = "No inference provider is available on the JoeOS backend."
                if availability:
                    reason = str(availability.get("reason") or reason)
                self._repository.update_run(
                    run_id, status="failed", now=self._now(), error_detail=reason
                )
                self._repository.complete_message(
                    assistant_id, status="failed", now=self._now(), error_detail=reason
                )
                yield {"event": "run.failed", "run_id": str(run_id), "conversation_id": str(conversation_id), "reason": reason}
                self._emit(principal, "run.failed", conversation_id=conversation_id, run_id=run_id, data={"reason": reason})
                return

            streaming = bool(availability.get("streaming"))
            provider_id = str(availability.get("provider_id") or "unknown")
            model = str(availability.get("model") or "")
            if streaming and self._stream_infer is not None:
                async for delta in self._stream_infer(wire_messages):
                    if cancel_event.is_set():
                        break
                    piece = delta if isinstance(delta, str) else (getattr(delta, "content", "") or "")
                    if not piece:
                        continue
                    accumulated += piece
                    self._emit(
                        principal,
                        "run.partial",
                        conversation_id=conversation_id,
                        run_id=run_id,
                        data={"chars": len(piece)},
                    )
                    yield {"event": "message.delta", "run_id": str(run_id), "content": piece}
            else:
                result = await self._infer(wire_messages)
                accumulated = getattr(result, "reply", "") or ""
                provider_id = getattr(result, "provider", None) or provider_id
                model = getattr(result, "model", None) or model
                if accumulated:
                    yield {"event": "message.delta", "run_id": str(run_id), "content": accumulated}

            if cancel_event.is_set():
                self._repository.update_run(run_id, status="cancelled", now=self._now())
                self._repository.complete_message(
                    assistant_id,
                    status="cancelled",
                    now=self._now(),
                    content=accumulated,
                    error_detail="cancelled by the operator",
                )
                yield {"event": "run.cancelled", "run_id": str(run_id), "conversation_id": str(conversation_id)}
                self._emit(principal, "run.cancelled", conversation_id=conversation_id, run_id=run_id)
                return

            self._repository.update_run(
                run_id,
                status="completed",
                now=self._now(),
                provider=provider_id,
                model=model,
            )
            self._repository.complete_message(
                assistant_id,
                status="completed",
                now=self._now(),
                content=accumulated,
                provider=provider_id,
                model=model,
            )
            yield {
                "event": "run.completed",
                "conversation_id": str(conversation_id),
                "run_id": str(run_id),
                "message_id": str(assistant_id),
                "content": accumulated,
            }
            self._emit(
                principal,
                "run.completed",
                conversation_id=conversation_id,
                run_id=run_id,
                data={"message_id": str(assistant_id)},
            )
        except asyncio.CancelledError:
            self._repository.update_run(run_id, status="cancelled", now=self._now())
            self._repository.complete_message(
                assistant_id,
                status="cancelled",
                now=self._now(),
                content=accumulated,
                error_detail="cancelled by the operator",
            )
            yield {"event": "run.cancelled", "run_id": str(run_id), "conversation_id": str(conversation_id)}
            self._emit(principal, "run.cancelled", conversation_id=conversation_id, run_id=run_id)
            raise
        except Exception as error:  # noqa: BLE001
            reason = str(error)[:240]
            self._repository.update_run(run_id, status="failed", now=self._now(), error_detail=reason)
            self._repository.complete_message(
                assistant_id, status="failed", now=self._now(), content=accumulated, error_detail=reason
            )
            yield {"event": "run.failed", "run_id": str(run_id), "conversation_id": str(conversation_id), "reason": reason}
            self._emit(principal, "run.failed", conversation_id=conversation_id, run_id=run_id, data={"reason": reason})
        finally:
            async with self._lock:
                self._cancelled.pop(run_id, None)

    # ------------------------------------------------------------------
    # Non-streaming inference
    # ------------------------------------------------------------------

    async def _run_inference(
        self,
        principal: Dict,
        conversation_id: UUID,
        user_message_id: UUID,
        parent_run_id: Optional[UUID],
    ) -> Dict:
        messages = self._repository.list_messages(conversation_id)
        completed = [message for message in messages if message.status == "completed"]
        bounded = completed[-40:]
        wire_messages = [
            {"role": message.role, "content": message.content} for message in bounded
        ]
        now = self._now()
        assistant_id = self._uuid()
        run_id = self._uuid()
        self._repository.append_message(
            message_id=assistant_id,
            conversation_id=conversation_id,
            role="assistant",
            content="",
            status="pending",
            now=now,
            parent_message_id=user_message_id,
        )
        self._repository.create_run(
            run_id=run_id,
            conversation_id=conversation_id,
            message_id=assistant_id,
            status="queued",
            now=now,
            parent_run_id=parent_run_id,
        )
        self._emit(
            principal,
            "run.queued",
            conversation_id=conversation_id,
            run_id=run_id,
            data={"message_id": str(assistant_id), "retry_of": str(parent_run_id) if parent_run_id else None},
        )
        task = asyncio.create_task(
            self._execute(principal, assistant_id, conversation_id, run_id, wire_messages)
        )
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
                self._repository.update_run(
                    run_id, status="cancelled", now=self._now(), error_detail="inference timed out"
                )
                self._repository.complete_message(
                    assistant_id, status="cancelled", now=self._now(), error_detail="inference timed out"
                )
            except asyncio.CancelledError:
                # The run was cancelled server-side; `_execute` recorded it.
                pass
        finally:
            async with self._lock:
                self._runs.pop(run_id, None)
        return self.get_conversation(principal, conversation_id)

    async def _execute(
        self,
        principal: Dict,
        assistant_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        wire_messages: List[Dict[str, str]],
    ) -> None:
        try:
            availability = self._availability()
            if availability is None or not availability.get("available"):
                reason = "No inference provider is available on the JoeOS backend."
                if availability:
                    reason = str(availability.get("reason") or reason)
                self._repository.update_run(run_id, status="failed", now=self._now(), error_detail=reason)
                self._repository.complete_message(
                    assistant_id, status="failed", now=self._now(), error_detail=reason
                )
                self._emit(principal, "run.failed", conversation_id=conversation_id, run_id=run_id, data={"reason": reason})
                return
            self._repository.update_run(run_id, status="running", now=self._now())
            self._emit(principal, "run.started", conversation_id=conversation_id, run_id=run_id)
            result = await self._infer(wire_messages)
            provider = getattr(result, "provider", None)
            model = getattr(result, "model", None)
            self._repository.update_run(
                run_id,
                status="completed",
                now=self._now(),
                provider=provider,
                model=model,
            )
            self._repository.complete_message(
                assistant_id,
                status="completed",
                now=self._now(),
                content=getattr(result, "reply", ""),
                provider=provider,
                model=model,
                tokens_used=getattr(result, "tokens_used", None),
            )
            self._emit(
                principal,
                "run.completed",
                conversation_id=conversation_id,
                run_id=run_id,
                data={"message_id": str(assistant_id)},
            )
        except asyncio.CancelledError:
            self._repository.update_run(run_id, status="cancelled", now=self._now())
            self._repository.complete_message(
                assistant_id, status="cancelled", now=self._now(), error_detail="cancelled by the operator"
            )
            self._emit(principal, "run.cancelled", conversation_id=conversation_id, run_id=run_id)
            raise
        except Exception as error:  # noqa: BLE001
            reason = str(error)[:240]
            self._repository.update_run(run_id, status="failed", now=self._now(), error_detail=reason)
            self._repository.complete_message(
                assistant_id, status="failed", now=self._now(), error_detail=reason
            )
            self._emit(principal, "run.failed", conversation_id=conversation_id, run_id=run_id, data={"reason": reason})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _accept_user_message(
        self,
        principal: Dict,
        conversation_id: UUID,
        text: str,
        idempotency_key: Optional[UUID],
    ) -> UUID:
        """Appends a user message exactly once. A replay with the same
        idempotency key returns the existing message id instead of duplicating."""
        if idempotency_key is not None:
            existing = self._repository.message_by_idempotency_key(idempotency_key)
            if existing is not None and existing.conversation_id == conversation_id:
                return existing.message_id
        user_message_id = self._uuid()
        self._repository.append_message(
            message_id=user_message_id,
            conversation_id=conversation_id,
            role="user",
            content=text,
            status="completed",
            now=self._now(),
            idempotency_key=idempotency_key,
        )
        self._emit(
            principal,
            "message.accepted",
            conversation_id=conversation_id,
            data={"message_id": str(user_message_id), "role": "user"},
        )
        return user_message_id

    def streaming_supported(self, principal: Dict) -> bool:
        availability = self._availability()
        return bool(availability and availability.get("streaming"))

    def conversation_events(
        self,
        principal: Dict,
        cursor: int,
        conversation_id: Optional[UUID] = None,
        limit: int = 50,
    ) -> tuple:
        """Cursor-resumable, workspace-scoped conversation events from the shared
        realtime event table. Returns (events, next_cursor)."""
        self._require(principal, "conversation.read")
        rows = self._repository.fetch_conversation_events_after(
            cursor, principal["workspace"]["id"], conversation_id, limit
        )
        events: List[Dict[str, object]] = []
        next_cursor = max(0, int(cursor))
        for row in rows:
            next_cursor = int(row["id"])
            try:
                payload = json.loads(str(row["message"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            events.append({"id": next_cursor, "event": payload})
        return events, next_cursor

    def _emit(
        self,
        principal: Dict,
        event: str,
        *,
        conversation_id: Optional[UUID] = None,
        run_id: Optional[UUID] = None,
        data: Optional[Dict[str, object]] = None,
    ) -> None:
        if self._event_sink is None:
            return
        envelope = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event": event,
            "org": str(principal["organization"]["id"]),
            "ws": str(principal["workspace"]["id"]),
            "user": str(principal["user"]["id"]),
            "conversation": str(conversation_id) if conversation_id else None,
            "run": str(run_id) if run_id else None,
            "ts": self._now(),
            "trace": str(self._uuid()),
        }
        if data:
            envelope["data"] = data
        message = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        self._event_sink("info", "conversations", message[:480])

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

    @staticmethod
    def _run_payload(run: object) -> Dict:
        return {
            "run_id": getattr(run, "run_id"),
            "conversation_id": getattr(run, "conversation_id"),
            "message_id": getattr(run, "message_id"),
            "status": getattr(run, "status"),
            "provider": getattr(run, "provider"),
            "model": getattr(run, "model"),
            "parent_run_id": getattr(run, "parent_run_id"),
            "created_at": getattr(run, "created_at"),
            "started_at": getattr(run, "started_at"),
            "terminal_at": getattr(run, "terminal_at"),
            "error_detail": getattr(run, "error_detail"),
        }


class _suppress:
    def __init__(self, *exceptions) -> None:
        self._exceptions = exceptions

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, self._exceptions)
