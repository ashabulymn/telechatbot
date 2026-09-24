from dataclasses import dataclass
from typing import Iterable

from ..attachments import Attachment


@dataclass(frozen=True)
class AttachmentRoute:
    attachment: Attachment
    route: str
    reason: str


def plan_attachment_routes(
    provider,
    attachments: Iterable[Attachment],
    model_capabilities: frozenset[str] = frozenset(),
    model_capabilities_known: bool = False,
) -> list[AttachmentRoute]:
    """Choose one deterministic processing route for every attachment.

    Routes are deliberately small: native, transcribe, fallback, disabled.
    Unknown model metadata never blocks a route.
    """
    routes = []
    for item in attachments:
        name = item.filename or item.kind
        mode = provider.attachment_mode(item)

        if mode == "disabled":
            routes.append(AttachmentRoute(item, "disabled", "disabled by provider settings"))
            continue

        if mode == "transcribe":
            if provider.supports_transcription(item):
                routes.append(AttachmentRoute(item, "transcribe", "provider requested transcription"))
            else:
                routes.append(AttachmentRoute(item, "fallback", "transcription requested but unavailable"))
            continue

        if item.kind in {"audio", "video"}:
            capability = item.kind
            if (
                provider.supports_transcription(item)
                and (
                    not model_capabilities_known
                    or capability not in model_capabilities
                )
            ):
                routes.append(AttachmentRoute(item, "transcribe", f"model lacks known {capability} input support"))
                continue

        if provider.supports_native_upload(item):
            routes.append(AttachmentRoute(item, "native", "native provider upload available"))
        else:
            routes.append(AttachmentRoute(item, "fallback", "generic attachment mapping"))

    return routes


def route_summary(routes: Iterable[AttachmentRoute]) -> str:
    labels = {
        "native": "native",
        "transcribe": "transkripsi",
        "fallback": "fallback",
        "disabled": "nonaktif",
    }
    lines = []
    for item in routes:
        name = item.attachment.filename or item.attachment.kind
        lines.append(f"• {name} → {labels.get(item.route, item.route)}")
    return "\n".join(lines)
