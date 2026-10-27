import argparse
import asyncio
import json
import os
from pathlib import Path

from .auth import forwarded_allow_ips
from .config import server_key
from .engine import ExtractionEngine, RunConfig, parse_pages
from .pdf_tools import PDFTools
from .provider import OpenRouter
from .schema import load_contract


def main():
    parser = argparse.ArgumentParser(description="Coastwise inspection workbench")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Open the local web application")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    sub.add_parser("models", help="List structured-output models on OpenRouter")
    inspect = sub.add_parser("inspect", help="Inspect PDF evidence without an API call")
    extract = sub.add_parser("extract", help="Run an extraction with OpenRouter")
    for command in [inspect, extract]:
        command.add_argument("pdf", type=Path)
        command.add_argument("--pages", default="")
        command.add_argument("--method", choices=["a", "b0", "b"], default="a")
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--no-ocr", action="store_true")
        command.add_argument("--contract", type=Path)
    extract.add_argument("--model", default=os.getenv("OPENROUTER_MODEL", ""))
    extract.add_argument("--provider", default=os.getenv("OPENROUTER_PROVIDER", ""))
    extract.add_argument("--prompt-file", type=Path)
    extract.add_argument("--max-usd", type=float, default=1)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        uvicorn.run("coastwise.web:app", host=args.host, port=args.port,
                    proxy_headers=True, forwarded_allow_ips=forwarded_allow_ips())
        return

    async def work():
        if args.command == "models":
            client = OpenRouter("", "")
            try:
                data = await client.models()
                print(json.dumps([{k: m.get(k) for k in ["id", "supported_parameters", "pricing"]}
                                  for m in data if "response_format" in m.get("supported_parameters", [])], indent=2))
            finally:
                await client.close()
            return
        if args.output.exists() and any(args.output.iterdir()):
            parser.error("Output directory must be empty to preserve existing results")
        pdf = PDFTools(args.pdf, args.output / "cache")
        config = RunConfig(method=args.method, pages=parse_pages(args.pages, len(pdf.doc)),
                           ocr=not args.no_ocr)
        client = None
        if args.command == "extract":
            config.model, config.provider, config.max_usd = args.model, args.provider, args.max_usd
            if args.prompt_file:
                config.system_prompt = args.prompt_file.read_text()
            client = OpenRouter(server_key(), config.model,
                                config.provider, config.max_usd)
        try:
            engine = ExtractionEngine(pdf, config, args.output, client,
                                      contract=load_contract(args.contract))
            result = await engine.run(preview=args.command == "inspect")
            print(json.dumps({"output": str(args.output.resolve()), "coverage": result["coverage"],
                              "preview_only": result["preview_only"], "failures": result["failures"]}))
        finally:
            pdf.close()
            if client:
                await client.close()
    asyncio.run(work())
