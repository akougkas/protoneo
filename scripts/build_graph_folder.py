"""Mini-only graph build for one folder containing one paper PDF."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from apps.paper_review.conference import load_profile
from apps.paper_review.manifest import domain_config
from protoneo.api.events import SessionEventBus
from protoneo.api.pipeline_control import PipelineControl
from protoneo.deliberation.session import SessionManager
from protoneo.knowledge.graph import KnowledgeGraph
from protoneo.knowledge.parser import extract_markdown_table_records, parse_file
from protoneo.knowledge.pipeline import GraphPipeline
from protoneo.llm.client import LLMClient
from protoneo.llm.registry import CapabilityRegistry
from protoneo.llm.settings import LocalEndpoint, load_settings, resolve_preset

MINI_MODEL = "Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M"
NVIDIA_ALIAS = "nvidia-nemotron-3-nano-omni-30b-a3b-reasoning"
MINI_PROFILE = "mini-nemotron-omni-graph"
MINI_ENDPOINT = "http://192.168.86.141:8080/v1"
SC26_IDS = ("pap111s2", "pap1162s2", "pap282s2", "pap440s2", "pap535s2", "pap616s2", "pap651s2")


def reject_dynamo(value: str) -> None:
    host = (urlparse(value).hostname or "").lower()
    if host == "192.168.86.143" or "dynamo" in value.lower():
        raise ValueError(f"Refusing Dynamo endpoint/model: {value}")


def endpoint_v1(value: str) -> str:
    reject_dynamo(value)
    value = value.strip().rstrip("/")
    if value.endswith("/chat/completions"):
        value = value[: -len("/chat/completions")]
    return value if value.endswith("/v1") else value + "/v1"


def model_id(value: str) -> str:
    value = (value or MINI_MODEL).strip()
    return MINI_MODEL if value.lower() == NVIDIA_ALIAS else value


def find_pdf(folder: Path) -> Path:
    pdfs = sorted(p for p in folder.glob("*.pdf") if not p.name.endswith("_details.pdf"))
    if len(pdfs) != 1:
        names = ", ".join(p.name for p in pdfs) or "none"
        raise ValueError(f"Expected exactly one paper PDF in {folder}; found {names}")
    return pdfs[0]


def mini_endpoint(settings, override: str) -> str:
    if override:
        return endpoint_v1(override)
    for ep in [*settings.localhost_endpoints, *settings.lan_endpoints]:
        if ep.id == "lan-mini" or ep.display_name.lower() == "mini":
            return endpoint_v1(ep.url)
    return MINI_ENDPOINT


def mini_settings(settings, endpoint: str, model: str):
    settings = settings.model_copy(deep=True)
    found = False
    for ep in settings.lan_endpoints:
        if ep.id == "lan-mini":
            ep.url = endpoint
            ep.enabled = True
            ep.type = "openai"
            found = True
        elif ep.id == "lan-dynamo":
            ep.enabled = False
    if not found:
        settings.lan_endpoints.append(
            LocalEndpoint(
                id="lan-mini",
                display_name="Mini",
                url=endpoint,
                type="openai",
                location="lan",
                enabled=True,
            )
        )
    settings.active_models["lan-mini"] = model
    settings.provider_enabled["lan-mini"] = True
    settings.provider_enabled["lan-dynamo"] = False
    models = settings.discovered_models.setdefault("lan-mini", [])
    if not any(isinstance(x, dict) and x.get("id") == model for x in models):
        models.append({
            "id": model,
            "source": "lan-mini",
            "owned_by": "llamacpp",
            "provider_type": "local",
            "tags": ["structured", "reasoning", "vision"],
            "architecture": {"input_modalities": ["text", "image"]},
        })
    return settings


def graph_models(settings, profile_name: str) -> dict[str, str]:
    preset = resolve_preset(profile_name, settings)
    if not preset:
        raise ValueError(f"Unknown model profile: {profile_name}")
    models = {k: preset.assignments.get(k, "") for k in ("ontology", "extraction", "coref", "verification")}
    for step, value in models.items():
        if not value.startswith("lan-mini/"):
            raise ValueError(f"{profile_name} is not Mini-only for {step}: {value}")
        reject_dynamo(value)
    return models


def vlm_config(endpoint: str, model: str) -> dict:
    return {
        "enabled": True,
        "url": endpoint + "/chat/completions",
        "model": model,
        "temperature": 0.1,
        "top_p": 0.9,
        "timeout": 180.0,
        "concurrency": 1,
        "prompt": (
            "Describe this scientific figure or table for a paper reviewer in 4-6 "
            "sentences. State chart/table type, axes or columns, compared methods, "
            "and key numeric results. Plain text only."
        ),
    }


def grounding_payload(graph, figures: list, tables: list) -> dict:
    artifacts = [x for x in [*(figures or []), *(tables or [])] if isinstance(x, dict)]
    described = sum(1 for x in artifacts if x.get("description"))
    total = len(artifacts)
    if total == 0:
        mode = "no_artifacts"
    elif described == 0:
        mode = "text_only"
    elif described == total:
        mode = "vision_grounded"
    else:
        mode = "mixed"
    visual_nodes = [n for n in graph.nodes if n.node_type in ("Figure", "Table")]
    return {
        **graph.grounding_summary(),
        "grounding_mode": mode,
        "visual_evidence_count": total,
        "graph_visual_node_count": len(visual_nodes),
        "extracted_figures": len(figures or []),
        "extracted_tables": len(tables or []),
        "described_visual_artifacts": described,
        "undescribed_visual_artifacts": total - described,
    }


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def visual_records_from_graph(graph: KnowledgeGraph, kind: str) -> list[dict]:
    records = []
    for index, node in enumerate((n for n in graph.nodes if n.node_type == kind), 1):
        attrs = dict(node.attributes or {})
        records.append({
            **attrs,
            "index": attrs.get("index", index),
            "kind": attrs.get("kind", kind.lower()),
            "caption": attrs.get("caption", ""),
            "source_text": node.source_text or attrs.get("caption", ""),
            "description": attrs.get("description") or node.description,
            "image_path": attrs.get("image_path", ""),
            "grounding": attrs.get("grounding", "extracted_no_vlm"),
        })
    return records


def refresh_payload_tables(data: dict, markdown: str) -> tuple[dict, int]:
    graph_dict = data.get("graph") if isinstance(data.get("graph"), dict) else data
    graph = KnowledgeGraph.model_validate(graph_dict)
    figures = visual_records_from_graph(graph, "Figure")
    tables = visual_records_from_graph(graph, "Table")
    new_tables = extract_markdown_table_records(
        markdown,
        start_index=len(tables) + 1,
        existing=tables,
    )
    if new_tables:
        graph.ingest_visual_evidence([], new_tables)
        tables.extend(new_tables)
        graph.summary = graph.to_agent_briefing()
        graph.update_stats()
    graph_data = graph.model_dump(mode="json")
    data.update(graph_data)
    data["graph"] = graph_data
    data.pop("links", None)
    data["grounding"] = grounding_payload(graph, figures, tables)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    return data, len(new_tables)


def manifest_payload(folder: Path, pdf: Path, markdown_path: Path, graph_path: Path, data: dict, warnings: list[str]) -> dict:
    grounding = data.get("grounding") or {}
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    return {
        "paper_id": folder.name,
        "source_pdf": str(pdf),
        "source_pdf_hash": file_hash(pdf) if pdf.exists() else "",
        "markdown_path": str(markdown_path),
        "markdown_hash": file_hash(markdown_path) if markdown_path.exists() else "",
        "graph_path": str(graph_path),
        "graph_hash": file_hash(graph_path) if graph_path.exists() else "",
        "created_at": data.get("created_at", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "model": data.get("model", MINI_MODEL),
        "endpoint": data.get("mini_endpoint", ""),
        "parser": "docling",
        "nodes": len(nodes),
        "edges": len(edges),
        "figure_nodes": sum(1 for n in nodes if n.get("node_type") == "Figure"),
        "table_nodes": sum(1 for n in nodes if n.get("node_type") == "Table"),
        "extracted_figures": grounding.get("extracted_figures", 0),
        "extracted_tables": grounding.get("extracted_tables", 0),
        "described_visual_artifacts": grounding.get("described_visual_artifacts", 0),
        "undescribed_visual_artifacts": grounding.get("undescribed_visual_artifacts", 0),
        "grounding_mode": grounding.get("grounding_mode", ""),
        "warnings": warnings,
        "source_session_id": data.get("source_session_id", ""),
        "import_identity": {
            "conference": data.get("conference", ""),
            "paper_title": data.get("paper_title", ""),
        },
    }


def write_manifest(folder: Path, pdf: Path, markdown_path: Path, graph_path: Path, data: dict, warnings: list[str]) -> Path:
    manifest_path = folder / "graph-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload(folder, pdf, markdown_path, graph_path, data, warnings), indent=2),
        encoding="utf-8",
    )
    return manifest_path


async def build(args: argparse.Namespace) -> dict:
    folder = Path(args.folder).expanduser().resolve()
    pdf = find_pdf(folder)
    markdown_path = folder / (args.markdown_name or f"{pdf.stem}.processed.md")
    graph_path = folder / (args.graph_name or f"{pdf.stem}.graph.json")
    if not args.force and (markdown_path.exists() or graph_path.exists()):
        raise FileExistsError(f"Refusing to overwrite output files in {folder}; pass --force")

    raw_settings = load_settings()
    model = model_id(args.model)
    endpoint = mini_endpoint(raw_settings, args.mini_endpoint)
    settings = mini_settings(raw_settings, endpoint, model)
    models = graph_models(settings, args.model_profile)
    profile = load_profile(args.conference)

    doc = parse_file(str(pdf), fast=False, vlm_config=vlm_config(endpoint, model))
    with tempfile.TemporaryDirectory(prefix="protoneo-graph-") as tmp:
        sessions = SessionManager(args.session_dir or tmp)
        client = LLMClient(
            registry=CapabilityRegistry.from_settings(settings),
            api_keys={"lan-mini": os.getenv("LLM_API_KEY", "sk-local")},
        )
        session = await sessions.create(
            config={"metadata": {
                "type": "graph_build",
                "conference": args.conference,
                "filename": pdf.name,
                "source_pdf": str(pdf),
                "model_profile": args.model_profile,
                "mini_endpoint": endpoint,
            }},
            app_name="paper_review",
            app_version="0.1.0",
        )
        session.document_text = doc.text
        session.document_markdown = doc.markdown or doc.text
        sessions.get_context(session.session_id).add_document(doc)
        await sessions.update(session)

        graph = await GraphPipeline(client, sessions, domain_config).run(
            session_id=session.session_id,
            document=doc,
            bus=SessionEventBus(),
            ctl=PipelineControl(),
            models=models,
            pruning_threshold=profile.graph_pruning_threshold,
            conference_context=f"{profile.name}: {profile.scope_text()}",
        )

    figures = (doc.metadata or {}).get("figures") or []
    tables = (doc.metadata or {}).get("tables") or []
    graph.ingest_visual_evidence(figures, tables)
    graph.summary = graph.to_agent_briefing()
    graph.update_stats()
    graph_data = graph.model_dump(mode="json")
    grounding = grounding_payload(graph, figures, tables)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_session_id": session.session_id,
        "source_pdf": str(pdf),
        "paper_title": graph.paper_title,
        "conference": args.conference,
        "model_profile": args.model_profile,
        "mini_endpoint": endpoint,
        "model": model,
        "document_markdown": doc.markdown or doc.text,
        "processed_markdown_path": str(markdown_path),
        "grounding": grounding,
        "graph": graph_data,
        **graph_data,
    }
    markdown_path.write_text(doc.markdown or doc.text, encoding="utf-8")
    graph_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_manifest(folder, pdf, markdown_path, graph_path, payload, [])
    return {
        "pdf": str(pdf),
        "markdown": str(markdown_path),
        "graph": str(graph_path),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "grounding": grounding,
    }


def plan_folder(folder: Path, args: argparse.Namespace) -> dict:
    pdf = find_pdf(folder)
    markdown_path = folder / (args.markdown_name or f"{pdf.stem}.processed.md")
    graph_path = folder / (args.graph_name or f"{pdf.stem}.graph.json")
    exists = markdown_path.exists() or graph_path.exists()
    return {
        "folder": str(folder),
        "pdf": str(pdf),
        "markdown": str(markdown_path),
        "graph": str(graph_path),
        "status": "would_overwrite" if exists and args.force else "exists" if exists else "planned",
    }


def validate_folder(folder: Path, args: argparse.Namespace) -> dict:
    from apps.paper_review.api import _parse_imported_graph_payload

    pdf = find_pdf(folder)
    markdown_path = folder / (args.markdown_name or f"{pdf.stem}.processed.md")
    graph_path = folder / (args.graph_name or f"{pdf.stem}.graph.json")
    if not graph_path.exists():
        return {"folder": str(folder), "pdf": str(pdf), "graph": str(graph_path), "status": "missing_graph"}

    data = json.loads(graph_path.read_text(encoding="utf-8"))
    imported = _parse_imported_graph_payload(data, filename=graph_path.name)
    graph = imported.graph
    visual = [n for n in graph.nodes if n.node_type in ("Figure", "Table")]
    missing_image = [
        n.label for n in visual
        if n.node_type == "Figure" and not n.attributes.get("image_path")
    ]
    missing_source = [
        n.label for n in visual
        if not (n.source_text or n.attributes.get("caption") or n.attributes.get("description"))
    ]
    grounding = data.get("grounding") or graph.grounding_summary()
    extracted = int(grounding.get("extracted_figures", 0)) + int(grounding.get("extracted_tables", 0))
    undescribed = int(grounding.get("undescribed_visual_artifacts", grounding.get("undescribed_artifact_count", 0)) or 0)
    warnings = []
    if "links" in data:
        warnings.append("top-level links key present")
    if "edges" not in data:
        warnings.append("top-level edges key missing")
    if not data.get("source_pdf"):
        warnings.append("source_pdf missing")
    if not data.get("grounding"):
        warnings.append("grounding block missing")
    if not visual:
        warnings.append("no Figure/Table visual evidence nodes")
    if not (data.get("document_markdown") or markdown_path.exists()):
        warnings.append("processed markdown unavailable")
    if not (folder / "graph-manifest.json").exists():
        warnings.append("graph-manifest.json missing")
    if missing_image:
        warnings.append(f"{len(missing_image)} visual nodes missing image_path")
    if missing_source:
        warnings.append(f"{len(missing_source)} visual nodes missing source_text")
    if extracted and len(visual) < extracted:
        warnings.append(f"{extracted - len(visual)} extracted visual artifacts missing graph nodes")
    if grounding.get("grounding_mode") == "vision_grounded" and undescribed:
        warnings.append("grounding_mode overstates vision coverage")
    return {
        "folder": str(folder),
        "pdf": str(pdf),
        "markdown": str(markdown_path),
        "graph": str(graph_path),
        "status": "valid" if not warnings else "warning",
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "figure_nodes": sum(1 for n in visual if n.node_type == "Figure"),
        "table_nodes": sum(1 for n in visual if n.node_type == "Table"),
        "extracted_figures": grounding.get("extracted_figures", 0),
        "extracted_tables": grounding.get("extracted_tables", 0),
        "described_visual_artifacts": grounding.get("described_visual_artifacts", grounding.get("described_artifact_count", 0)),
        "undescribed_visual_artifacts": grounding.get("undescribed_visual_artifacts", grounding.get("undescribed_artifact_count", 0)),
        "grounding_mode": grounding.get("grounding_mode", ""),
        "warnings": warnings,
    }


def refresh_existing_folder(folder: Path, args: argparse.Namespace) -> dict:
    pdf = find_pdf(folder)
    markdown_path = folder / (args.markdown_name or f"{pdf.stem}.processed.md")
    graph_path = folder / (args.graph_name or f"{pdf.stem}.graph.json")
    if not graph_path.exists() or not markdown_path.exists():
        return {
            "folder": str(folder),
            "pdf": str(pdf),
            "graph": str(graph_path),
            "markdown": str(markdown_path),
            "status": "missing_graph_or_markdown",
        }
    data = json.loads(graph_path.read_text(encoding="utf-8"))
    data, tables_added = refresh_payload_tables(data, markdown_path.read_text(encoding="utf-8"))
    graph_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    validation = validate_folder(folder, args)
    write_manifest(folder, pdf, markdown_path, graph_path, data, validation.get("warnings", []))
    validation = validate_folder(folder, args)
    write_manifest(folder, pdf, markdown_path, graph_path, data, validation.get("warnings", []))
    return {**validation, "tables_added": tables_added}


async def run(args: argparse.Namespace) -> dict:
    raw_settings = load_settings()
    model = model_id(args.model)
    endpoint = mini_endpoint(raw_settings, args.mini_endpoint)
    settings = mini_settings(raw_settings, endpoint, model)
    graph_models(settings, args.model_profile)

    if args.sc26_all:
        root = Path(args.packet_root).expanduser().resolve()
        folders = [root / paper_id for paper_id in SC26_IDS]
    else:
        folders = [Path(args.folder).expanduser().resolve()]

    if args.dry_run:
        return {
            "dry_run": True,
            "model_profile": args.model_profile,
            "model": model,
            "mini_endpoint": endpoint,
            "results": [plan_folder(folder, args) for folder in folders],
        }

    if args.validate:
        return {
            "validate": True,
            "model_profile": args.model_profile,
            "model": model,
            "mini_endpoint": endpoint,
            "results": [validate_folder(folder, args) for folder in folders],
        }

    if args.refresh_existing:
        return {
            "refresh_existing": True,
            "model_profile": args.model_profile,
            "model": model,
            "mini_endpoint": endpoint,
            "results": [refresh_existing_folder(folder, args) for folder in folders],
        }

    results = []
    for folder in folders:
        per_folder_args = argparse.Namespace(**{**vars(args), "folder": str(folder)})
        results.append(await build(per_folder_args))
    return {
        "dry_run": False,
        "model_profile": args.model_profile,
        "model": model,
        "mini_endpoint": endpoint,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build markdown and graph from one PDF folder using Mini only")
    parser.add_argument("folder", nargs="?")
    parser.add_argument("--sc26-all", action="store_true")
    parser.add_argument("--packet-root", default="submission_packets_sc26")
    parser.add_argument("--conference", default="sc26")
    parser.add_argument("--model-profile", default=MINI_PROFILE)
    parser.add_argument("--mini-endpoint", default="")
    parser.add_argument("--model", default=MINI_MODEL)
    parser.add_argument("--markdown-name", default="")
    parser.add_argument("--graph-name", default="")
    parser.add_argument("--session-dir", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--refresh-existing", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.sc26_all and not args.folder:
        parser.error("folder is required unless --sc26-all is passed")
    print(json.dumps(asyncio.run(run(args)), indent=2))


if __name__ == "__main__":
    main()
