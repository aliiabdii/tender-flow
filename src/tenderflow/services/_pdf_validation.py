import resource
import sys


def main() -> int:
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    from pypdf import PdfReader

    try:
        pdf = PdfReader(sys.stdin.buffer, strict=True)
        if pdf.is_encrypted or (
            pdf.metadata and pdf.metadata.get("/TenderFlowGeneratedArtifact")
        ):
            return 1
        if not 1 <= len(pdf.pages) <= 500:
            return 1
        expanded = 0
        has_text = False
        for page in pdf.pages:
            content = page.get_contents()
            if content is not None:
                expanded += len(content.get_data())
                if expanded > 50 * 1024 * 1024:
                    return 1
            has_text = bool(page.extract_text().strip()) or has_text
        return 0 if has_text else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
