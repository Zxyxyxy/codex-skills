"""Build a portable, allowlisted skill ZIP; never package user work files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "SKILL.md", "agents/openai.yaml", "requirements.txt", "assets/layout.json",
    "references/content-rules.md", "references/layout.md", "references/local-install.md",
    "references/review-schema.md", "references/conversation.md", "scripts/resume_fix.py", "scripts/make_example.py",
    "scripts/package_skill.py", "tests/test_resume_fix.py", "tests/fixtures/resume-pm-recsys.txt",
]


def package(out):
    out = Path(out)
    if out.suffix.lower() != ".zip":
        raise ValueError("Package output must be a .zip file")
    names = FILES + (["assets/example-preview.png"] if (ROOT / "assets/example-preview.png").is_file() else [])
    payloads, hashes = {}, {}
    for relative in names:
        file = ROOT / relative
        if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError(f"Missing or unsafe package input: {relative}")
        data = file.read_bytes()
        if file.suffix != ".png":
            text = data.decode("utf-8")
            # Split host-path literals so this scanner does not match its own
            # source code when packaging this script.
            sensitive = (r"/" + r"Users/[^/\s]+/|/private/" +
                         r"tmp/|https?://[^\s]*-int\.|(?i:sk-)[A-Za-z0-9]{20,}")
            if re.search(sensitive, text):
                raise ValueError(f"Host-specific path or sensitive configuration in {relative}")
        payloads[relative] = data
        hashes[relative] = hashlib.sha256(data).hexdigest()
    manifest = {"name": "resume-fix", "version": "1.1.2", "sha256": hashes,
                "contains_real_resumes": False, "contains_model_credentials": False,
                "example_source": "Synthetic fixture, not a model run"}
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in payloads.items():
            info = zipfile.ZipInfo("resume-fix/" + relative, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
        archive.writestr("resume-fix/PACKAGE-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(out) as archive:
        for relative, checksum in hashes.items():
            assert hashlib.sha256(archive.read("resume-fix/" + relative)).hexdigest() == checksum
    return {"zip": str(out), "files": len(hashes)+1, "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.out), ensure_ascii=False))
