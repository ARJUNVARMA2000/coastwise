import asyncio
import json
import zipfile

import httpx
import pytest

from coastwise.engine import ExtractionEngine, RunConfig, validate_rows
from coastwise.pdf_tools import PDFTools
from coastwise.provider import ModelError, OpenRouter, parse_completion
from coastwise.schema import load_contract, output_schema


def extraction():
    row = {name: {"value": None, "status": "NR", "evidence": []}
           for name in load_contract()["tables"]["observations"]}
    row["finding"] = {"value": "Moderate corrosion", "status": "reported",
                      "evidence": [{"ref": "p1:native_text:0", "quote": "moderate corrosion"}]}
    return {"structures": [], "reports": [], "observations": [row]}


class FakeClient:
    def __init__(self, messages=None):
        self.messages = list(messages or [{"content": json.dumps(extraction())}])
        self.calls, self.logs = [], []

    async def preflight(self, **kwargs):
        self.preflight_tools = kwargs["tools"]

    async def call(self, messages, **kwargs):
        self.calls.append((messages.copy(), kwargs))
        return self.messages.pop(0)


def test_validation():
    data = extraction()
    evidence = {"p1:native_text:0": {"text": "moderate corrosion"}}
    schema = output_schema(load_contract())
    assert not validate_rows(data, schema, evidence)
    data["observations"][0]["finding"]["evidence"][0]["quote"] = "severe corrosion"
    assert validate_rows(data, schema, evidence)[0]["code"] == "quote"
    data["observations"][0]["finding"]["status"] = "NR"
    assert any(x["code"] == "null_code" for x in validate_rows(data, schema, evidence))
    assert validate_rows({"invalid": []}, schema, evidence)[0]["code"] == "schema"


def test_a_uses_one_pass_and_exports(source_pdf, tmp_path):
    pdf, client = PDFTools(source_pdf), FakeClient()
    config = RunConfig(pages=[1], model="test/model", chunk_chars=40000)
    directory = tmp_path / "run"
    result = asyncio.run(ExtractionEngine(pdf, config, directory, client).run())
    assert len(client.calls) == 1
    assert not client.preflight_tools
    assert result["tables"]["observations"][0]["fields"]["finding"]["value"] == "Moderate corrosion"
    assert "Moderate corrosion" in (directory / "observations.csv").read_text()
    assert "source_report_id" in (directory / "structures.csv").read_text()
    with zipfile.ZipFile(directory / "outputs.zip") as z:
        assert len(z.namelist()) == 5
    assert all(e["kind"] == "native_text" for e in result["evidence"].values())
    pdf.close()


def test_b_modes_share_input_and_loop_is_bounded(source_pdf, tmp_path):
    pdf = PDFTools(source_pdf)
    b0 = ExtractionEngine(pdf, RunConfig(method="b0", pages=[1]), tmp_path / "b0")
    b = ExtractionEngine(pdf, RunConfig(method="b", pages=[1], max_requests=2), tmp_path / "b")
    assert b0.prepare()[1] == b.prepare()[1]
    invalid = extraction()
    invalid["observations"][0]["finding"]["evidence"][0]["quote"] = "invented"
    client = FakeClient([{"content": json.dumps(invalid)}, {"content": "No additional tools"}])
    b.client = client
    result = asyncio.run(b.run())
    assert len(client.calls) == 2
    assert result["failures"][0]["message"] == "Maximum model requests reached"
    assert b.client.preflight_tools
    with pytest.raises(ValueError, match="outside"):
        b.dispatch("get_page_text", '{"page":2}')
    with pytest.raises(ValueError, match="not allowed"):
        b.dispatch("shell", '{}')
    pdf.close()


def test_preview_needs_no_client(source_pdf, tmp_path):
    pdf = PDFTools(source_pdf)
    result = asyncio.run(ExtractionEngine(pdf, RunConfig(pages=[2]), tmp_path / "preview").run(preview=True))
    assert result["preview_only"] and result["requests"] == []
    assert result["evidence"]["p2:native_text:0"]["page"] == 2
    pdf.close()


def test_provider_schema_and_secret_handling():
    def handler(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json={"data": [{"id": "test/model", "supported_parameters": ["tools", "response_format"], "pricing": {"prompt":"0.000001", "completion":"0.000001"}}]})
        body = json.loads(request.content)
        assert body["provider"]["require_parameters"]
        assert body["provider"]["only"] == ["test-provider"]
        assert body["response_format"]["json_schema"]["strict"]
        return httpx.Response(200, json={"model":"test/model", "provider":"test-provider", "usage":{"cost":0.001},
                                        "choices":[{"finish_reason":"stop", "message":{"content":"{}"}}]})
    async def work():
        client = OpenRouter("secret-test-key", "test/model", "test-provider", transport=httpx.MockTransport(handler))
        await client.preflight(tools=True)
        assert await client.call([], schema={}) == {"content":"{}"}
        assert "secret-test-key" not in json.dumps(client.logs)
        await client.close()
    asyncio.run(work())
    with pytest.raises(ModelError):
        parse_completion({"content":"not json"})


def test_b_repairs_with_targeted_tools(source_pdf, tmp_path):
    invalid = extraction()
    invalid["observations"][0]["finding"]["evidence"][0]["quote"] = "invented"
    corrected = extraction()
    tool = {"content": None, "tool_calls": [{"id": "call-1", "type": "function",
            "function": {"name":"get_page_text", "arguments":'{"page":2}'}}]}
    client = FakeClient([{"content":json.dumps(invalid)}, tool,
                         {"content":json.dumps(corrected)}])
    pdf = PDFTools(source_pdf)
    engine = ExtractionEngine(pdf, RunConfig(method="b", pages=[1,2], max_rounds=1),
                              tmp_path / "repair", client)
    result = asyncio.run(engine.run())
    assert result["tool_events"][0]["result"]["page"] == 2
    assert len(client.calls) == 3 and result["failures"] == []
    assert not any(i["code"] == "quote" for i in result["issues"])
    assert any(m.get("tool_call_id") == "call-1" for m in client.calls[-1][0])
    pdf.close()


def test_cost_cap_blocks_before_request_and_truncation_is_rejected():
    posts = []
    def handler(request):
        posts.append(request)
        return httpx.Response(200, json={"choices":[{"finish_reason":"length", "message":{"content":"{}"}}]})
    async def work():
        client = OpenRouter("key", "model", max_usd=0.000000001,
                            transport=httpx.MockTransport(handler))
        client.pricing = {"prompt":"0.001", "completion":"0.001"}
        with pytest.raises(ModelError, match="budget"):
            await client.call([])
        assert posts == []
        client.max_usd = 100
        with pytest.raises(ModelError, match="length"):
            await client.call([])
        await client.close()
    asyncio.run(work())

