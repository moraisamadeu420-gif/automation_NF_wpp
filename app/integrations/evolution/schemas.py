"""
app/integrations/evolution/schemas.py
Pydantic models for Evolution API webhook payloads.

Evolution API v2 wraps real messages inside a list under data.messages[0]
for MESSAGES_UPSERT events. Both flat and nested structures are handled.
"""
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MessageKey(BaseModel):
    remote_jid: str = Field(alias="remoteJid")
    from_me: bool = Field(alias="fromMe", default=False)
    id: str = ""

    model_config = {"populate_by_name": True, "extra": "ignore"}


class ImageMessage(BaseModel):
    url: str | None = None
    mime_type: str | None = Field(default=None, alias="mimetype")
    file_length: str | None = Field(default=None, alias="fileLength")
    media_key: str | None = Field(default=None, alias="mediaKey")
    jpeg_thumbnail: str | None = Field(default=None, alias="jpegThumbnail")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class MessageContent(BaseModel):
    conversation: str | None = None
    image_message: ImageMessage | None = Field(default=None, alias="imageMessage")
    extended_text_message: dict | None = Field(default=None, alias="extendedTextMessage")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @property
    def text(self) -> str | None:
        if self.conversation:
            return self.conversation
        if self.extended_text_message:
            return self.extended_text_message.get("text")
        return None

    @property
    def has_image(self) -> bool:
        return self.image_message is not None


class WebhookMessage(BaseModel):
    key: MessageKey
    message: MessageContent | None = None
    message_type: str | None = Field(default=None, alias="messageType")
    push_name: str | None = Field(default=None, alias="pushName")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class WebhookEvent(BaseModel):
    event: str
    instance: str = ""
    data: dict[str, Any] | list[Any] = {}

    model_config = {"extra": "ignore"}

    def extract_messages(self) -> list[WebhookMessage]:
        """
        Evolution API v2 sends MESSAGES_UPSERT with data as a list of message
        objects, or as a dict with a 'messages' key containing the list.
        Handle all known variants.
        """
        from loguru import logger

        candidates: list[dict] = []

        if isinstance(self.data, list):
            candidates = [item for item in self.data if isinstance(item, dict)]
        elif isinstance(self.data, dict):
            inner = self.data.get("messages")
            if isinstance(inner, list):
                candidates = [item for item in inner if isinstance(item, dict)]
            else:
                candidates = [self.data]

        messages: list[WebhookMessage] = []
        for item in candidates:
            try:
                messages.append(WebhookMessage.model_validate(item))
            except Exception as exc:
                logger.warning("Could not parse message item — {}\nitem: {}", exc, str(item)[:300])

        return messages

