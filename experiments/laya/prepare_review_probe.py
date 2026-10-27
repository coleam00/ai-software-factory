"""Generate observation-only workflow slices in a disposable pinned Archon clone."""
import argparse
import copy
from pathlib import Path
import shlex
import shutil
import subprocess

PIN = "24796870605b0fd576734b79a8d5c781f2b1c1e1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archon", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    import yaml
    source = args.archon.resolve()
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if head != PIN:
        raise ValueError("use a disposable Archon clone at the factory's evaluated pin")
    owner = source / ".archon/workflows/sdlc/deliver"
    # The source slice must match the pinned commit, not merely its HEAD label.
    for name in ("archon-deliver.yaml", "commands/classify-review-scope.md",
                 "scripts/resolve-review-scope.py"):
        relative = (owner / name).relative_to(source).as_posix()
        committed = subprocess.check_output(["git", "-C", str(source), "show", f"{PIN}:{relative}"])
        if (owner / name).read_bytes() != committed:
            raise ValueError(f"source differs from pinned commit: {relative}")
    flow = yaml.safe_load((owner / "archon-deliver.yaml").read_text())
    classify = copy.deepcopy(next(n for n in flow["nodes"] if n["id"] == "classify"))
    classify.pop("depends_on")
    resolver = copy.deepcopy(next(n for n in flow["nodes"] if n["id"] == "resolve-scope"))
    baseline = {"name": "laya-review-baseline", "description": "Read-only classifier and resolver slice; no reviews.",
                "inputs": {"errors": {"default": "auto"}}, "returns": "resolve-scope",
                "nodes": [classify, resolver]}
    local = copy.deepcopy(baseline)
    local["name"] = "laya-review-local"
    local["description"] = "Observation only, including uncertain predictions; never executes a review."
    local["inputs"].update({key: {"required": True} for key in ("repo", "pr", "report")})
    script = Path(__file__).resolve().with_name("review_probe.py")
    local["nodes"][0] = {"id": "classify", "bash": (
        # Resolving a venv's Python symlink would bypass that virtual environment.
        f'{shlex.quote(str(args.python.absolute()))} {shlex.quote(str(script))} '
        '--repo "$INPUTS_REPO" --pr "$INPUTS_PR" --report "$INPUTS_REPORT"')}
    local["nodes"][1]["when"] = "$classify.output.status == 'predicted'"
    for node_id, value in (("forced-on", "true"), ("forced-off", "false")):
        node = copy.deepcopy(local["nodes"][1])
        node["id"] = node_id
        node["with"]["errors"] = value
        local["nodes"].append(node)
    root = source / ".archon/workflows/experimental"
    targets = [root / f["name"] for f in (baseline, local)]
    if any(p.exists() for p in targets):
        raise ValueError("probe folders already exist; use a fresh disposable clone")
    for folder, workflow in zip(targets, (baseline, local)):
        (folder / "scripts").mkdir(parents=True)
        shutil.copyfile(owner / "scripts/resolve-review-scope.py", folder / "scripts/resolve-review-scope.py")
        if workflow is baseline:
            (folder / "commands").mkdir()
            shutil.copyfile(owner / "commands/classify-review-scope.md", folder / "commands/classify-review-scope.md")
        (folder / f'{workflow["name"]}.yaml').write_text(yaml.safe_dump(workflow, sort_keys=False))
    print("Generated two observation-only workflows. Native provider configuration is unchanged.")


if __name__ == "__main__":
    main()
