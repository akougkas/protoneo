"""Plain text parser."""

from pathlib import Path

from ..types import ParseResult


def _read_text_with_fallback(file_path: str) -> str:
    data = Path(file_path).read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    encoding = None
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best and best.encoding:
            encoding = best.encoding
    except Exception:
        pass

    if not encoding:
        try:
            import chardet

            result = chardet.detect(data)
            encoding = result.get("encoding") if result else None
        except Exception:
            pass

    return data.decode(encoding or "utf-8", errors="replace")


class PlainTextParser:
    """Reads plain text files with charset detection fallback."""

    @property
    def name(self) -> str:
        return "plaintext"

    @property
    def supported_extensions(self) -> set[str]:
        return {".txt", ".text"}

    def available(self) -> bool:
        return True

    async def parse(self, path: Path, options: dict | None = None) -> ParseResult:
        text = _read_text_with_fallback(str(path))
        return ParseResult(text=text)
