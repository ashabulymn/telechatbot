from app.providers.routing import plan_attachment_routes, route_summary


class Item:
    def __init__(self, kind, name):
        self.kind = kind
        self.filename = name


class Provider:
    def __init__(self, modes=None, native=None, transcription=None):
        self.modes = modes or {}
        self.native = set(native or [])
        self.transcription = set(transcription or [])

    def attachment_mode(self, item):
        return self.modes.get(item.kind, "auto")

    def supports_native_upload(self, item):
        return item.kind in self.native

    def supports_transcription(self, item):
        return item.kind in self.transcription


def test_routing_prefers_transcription_when_model_lacks_audio():
    item = Item("audio", "voice.ogg")
    provider = Provider(native={"audio"}, transcription={"audio"})
    routes = plan_attachment_routes(provider, [item], frozenset({"text"}), True)
    assert routes[0].route == "transcribe"


def test_routing_uses_native_when_model_supports_audio():
    item = Item("audio", "voice.ogg")
    provider = Provider(native={"audio"}, transcription={"audio"})
    routes = plan_attachment_routes(provider, [item], frozenset({"audio"}), True)
    assert routes[0].route == "native"


def test_routing_respects_disabled_and_explicit_transcribe():
    items = [Item("video", "clip.mp4"), Item("audio", "voice.ogg")]
    provider = Provider(
        modes={"video": "disabled", "audio": "transcribe"},
        transcription={"audio"},
    )
    routes = plan_attachment_routes(provider, items)
    assert [r.route for r in routes] == ["disabled", "transcribe"]


def test_routing_summary_is_user_readable():
    item = Item("document", "report.pdf")
    routes = plan_attachment_routes(Provider(), [item])
    assert "report.pdf → fallback" in route_summary(routes)
