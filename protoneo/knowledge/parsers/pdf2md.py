"""AI-powered PDF extraction via the pdf2md CLI tool."""

import logging
import os
import subprocess
from pathlib import Path

from ..types import ParseResult

logger = logging.getLogger("protoneo.knowledge.parsers.pdf2md")

_PDF2MD_REPO = Path.home() / "tools" / "paper-to-md"


class Pdf2MdParser:
    """Extracts structured markdown from PDFs using the pdf2md CLI.

    Calls pdf2md as a subprocess with the local AI pipeline (Nemotron for
    text reasoning, Qwen3-VL for figure descriptions). Produces proper section
    headers, linked citations, reflowed paragraphs, and VLM figure descriptions.
    """

    @property
    def name(self) -> str:
        return "pdf2md"

    @property
    def supported_extensions(self) -> set[str]:
        return {".pdf"}

    def available(self) -> bool:
        return (_PDF2MD_REPO / ".venv" / "bin" / "pdf2md").exists()

    async def parse(self, path: Path, options: dict | None = None) -> ParseResult:
        options = options or {}
        output_dir = options.get("output_dir")

        if output_dir:
            out_dir = Path(output_dir)
        else:
            sessions_dir = Path(__file__).resolve().parents[3] / "data" / "sessions" / "pdf2md"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            out_dir = sessions_dir

        env = os.environ.copy()
        try:
            from ...llm.settings import load_settings, endpoint_map
            settings = load_settings()
            endpoints = endpoint_map(settings)
            dynamo = endpoints.get("lan-dynamo")
            if dynamo:
                env.setdefault("LM_STUDIO_HOST", dynamo.url)
        except Exception:
            pass
        env.setdefault("LM_STUDIO_HOST", "http://192.168.86.143:1234/v1")
        env.setdefault("PDF2MD_TEXT_MODEL", "nemotron-cascade-2-30b-a3b-i1")
        env.setdefault("PDF2MD_VLM_HOST", "http://192.168.86.141:8080/v1")
        env.setdefault("PDF2MD_VLM_MODEL", "Qwen3-VL-30B-A3B-Thinking-Q5_K_XL")

        cmd = [
            str(_PDF2MD_REPO / ".venv" / "bin" / "pdf2md"),
            "convert",
            str(path),
            str(out_dir),
            "--depth", "high",
            "--local",
            "--keep-raw",
        ]

        logger.info("Running pdf2md: %s", " ".join(cmd[:6]))

        try:
            result = subprocess.run(
                cmd,
                env=env,
                cwd=str(_PDF2MD_REPO),
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                logger.warning("pdf2md failed (exit %d): %s", result.returncode, result.stderr[:500])
                raise RuntimeError(f"pdf2md exit code {result.returncode}")

            stem = path.stem
            md_path = out_dir / stem / f"{stem}.md"
            if md_path.exists():
                md = md_path.read_text(encoding="utf-8")
                figures_dir = str(out_dir / stem / "img")
                logger.info("pdf2md produced %d chars of markdown from %s", len(md), path.name)
                return ParseResult(text="", markdown=md, figures_dir=figures_dir)

            raise RuntimeError(f"pdf2md output not found at {md_path}")

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"pdf2md timed out after 600s for {path.name}")
