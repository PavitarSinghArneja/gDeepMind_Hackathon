import json

import httpx
import pytest

from snapsort.llm import LLMBadOutput, LLMUnavailable, OllamaLLM, generate_with_fallback, installed

SCHEMA = {"title": "t", "type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def client(handler):
    return OllamaLLM("http://ollama", "embed", 5, transport=httpx.MockTransport(handler))


def ok(_req):
    return httpx.Response(200, json={"message": {"content": '{"a": "ok"}'}})


def test_generate_json_sends_schema_images_and_zero_temperature():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return ok(req)

    assert client(handler).generate_json("m", "hi", SCHEMA, [b"img"]) == {"a": "ok"}
    assert seen["format"] == SCHEMA
    assert seen["messages"][0]["images"] == ["aW1n"]
    assert seen["options"]["temperature"] == 0
    assert seen["stream"] is False


def test_missing_required_key_is_bad_output():
    handler = lambda req: httpx.Response(200, json={"message": {"content": '{"b": 1}'}})
    with pytest.raises(LLMBadOutput):
        client(handler).generate_json("m", "hi", SCHEMA)


def test_non_json_is_bad_output():
    handler = lambda req: httpx.Response(200, json={"message": {"content": "sure! here you go"}})
    with pytest.raises(LLMBadOutput):
        client(handler).generate_json("m", "hi", SCHEMA)


def test_connection_refused_is_unavailable():
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailable):
        client(handler).generate_json("m", "hi", SCHEMA)


def test_model_not_pulled_is_unavailable():
    handler = lambda req: httpx.Response(404, json={"error": "model 'gemma4:e4b' not found"})
    with pytest.raises(LLMUnavailable):
        client(handler).generate_json("m", "hi", SCHEMA)


def test_image_rejection_retries_text_only_and_remembers():
    calls = []

    def handler(req):
        has_images = "images" in json.loads(req.content)["messages"][0]
        calls.append(has_images)
        if has_images:
            return httpx.Response(500, json={"error": "this model does not support image input"})
        return ok(req)

    llm = client(handler)
    assert llm.generate_json("m", "hi", SCHEMA, [b"x"]) == {"a": "ok"}
    assert llm.generate_json("m", "hi", SCHEMA, [b"x"]) == {"a": "ok"}
    assert calls == [True, False, False]
    assert llm.vision is False


def test_fallback_uses_next_model_on_timeout():
    def handler(req):
        if json.loads(req.content)["model"] == "big":
            raise httpx.ReadTimeout("slow")
        return ok(req)

    hops = []
    out, model = generate_with_fallback(client(handler), ["big", "small"], "hi", SCHEMA, on_fallback=lambda m, e: hops.append(m))
    assert out == {"a": "ok"} and model == "small" and hops == ["big"]


def test_fallback_never_swallows_unavailable():
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailable):
        generate_with_fallback(client(handler), ["big", "small"], "hi", SCHEMA)


def test_embed_and_models():
    def handler(req):
        if req.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        return httpx.Response(200, json={"models": [{"name": "gemma4:e2b"}, {"name": "embeddinggemma:latest"}]})

    llm = client(handler)
    assert llm.embed("x") == [0.1, 0.2]
    names = llm.models()
    assert installed(names, "gemma4:e2b") and installed(names, "embeddinggemma") and not installed(names, "gemma4:e4b")
