"""Export SPACE (STEP + fine STL) and KEEP_* (fine STL) from the TopoTestBeam Part Studio.

    python -m pipeline.onshape_export [--out geometry]

Needs ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY in the environment and the IDs in config/onshape.yaml.
Writes geometry/space.step, geometry/space.stl, geometry/keep_parts/KEEP_*.stl, geometry/export.json.
Endpoint paths and parameter names are marked (verify) in the plan; they are kept in one place here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

from pipeline.config import REPO, bores, load_config

ONSHAPE_CONFIG = REPO / "config" / "onshape.yaml"


class Onshape:
    """Minimal REST client: basic auth with API keys, manual redirect handling."""

    def __init__(self, base_url: str, api_version: str, access: str, secret: str):
        self.base = base_url.rstrip("/")
        self.v = api_version
        self.s = requests.Session()
        self.s.auth = (access, secret)

    def url(self, path: str) -> str:
        return f"{self.base}/api/{self.v}/{path.lstrip('/')}"

    def _send(self, method: str, url: str, **kw) -> requests.Response:
        r = self.s.request(method, url, allow_redirects=False, timeout=120, **kw)
        # Exports redirect to a download URL; keep credentials only on the same host.
        for _ in range(5):
            if r.status_code not in (301, 302, 303, 307, 308):
                break
            loc = r.headers["Location"]
            same = urlparse(loc).netloc in ("", urlparse(self.base).netloc)
            r = (self.s if same else requests).get(loc, allow_redirects=False, timeout=300,
                                                    headers=kw.get("headers"))
        if not r.ok:
            raise RuntimeError(f"{method} {url} -> HTTP {r.status_code}: {r.text[:500]}")
        return r

    def get(self, path: str, params=None, accept="application/json") -> requests.Response:
        return self._send("GET", self.url(path), params=params, headers={"Accept": accept})

    def post(self, path: str, body: dict) -> requests.Response:
        return self._send("POST", self.url(path), json=body,
                          headers={"Accept": "application/json", "Content-Type": "application/json"})


def _dwe(oc: dict) -> str:
    return f"d/{oc['document_id']}/w/{oc['workspace_id']}/e/{oc['element_id']}"


def part_ids_by_name(api: Onshape, oc: dict) -> dict[str, str]:
    parts = api.get(f"parts/{_dwe(oc)}").json()
    out = {}
    for p in parts:
        if p["name"] in out:
            raise RuntimeError(f"duplicate part name {p['name']!r} in the Part Studio")
        out[p["name"]] = p["partId"]
    return out


def export_stl(api: Onshape, oc: dict, part_id: str, dest: Path) -> None:
    r = api.get(f"parts/{_dwe(oc)}/partid/{part_id}/stl", params=oc["stl_params"], accept="*/*")
    dest.write_bytes(r.content)


def export_step(api: Onshape, oc: dict, part_id: str, dest: Path, poll_s: float = 2.0, timeout_s: float = 600) -> None:
    body = {"formatName": "STEP", "partIds": part_id, "storeInDocument": False,
            "flattenAssemblies": False, "yAxisIsUp": False}
    tr = api.post(f"partstudios/{_dwe(oc)}/translations", body).json()
    t0 = time.time()
    while tr.get("requestState") == "ACTIVE":
        if time.time() - t0 > timeout_s:
            raise RuntimeError(f"STEP translation {tr['id']} timed out")
        time.sleep(poll_s)
        tr = api.get(f"translations/{tr['id']}").json()
    if tr.get("requestState") != "DONE":
        raise RuntimeError(f"STEP translation failed: {tr.get('failureReason') or tr}")
    fid = tr["resultExternalDataIds"][0]
    r = api.get(f"documents/d/{oc['document_id']}/externaldata/{fid}", accept="*/*")
    dest.write_bytes(r.content)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "geometry")
    a = ap.parse_args(argv)

    cfg = load_config()
    oc = yaml.safe_load(ONSHAPE_CONFIG.read_text(encoding="utf-8"))
    missing = [k for k in ("document_id", "workspace_id", "element_id") if not oc.get(k)]
    if missing:
        print(f"config/onshape.yaml is missing: {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        api = Onshape(oc["base_url"], oc["api_version"], os.environ["ONSHAPE_ACCESS_KEY"],
                      os.environ["ONSHAPE_SECRET_KEY"])
    except KeyError as e:
        print(f"environment variable {e} is not set", file=sys.stderr)
        return 2

    ids = part_ids_by_name(api, oc)
    wanted = ["SPACE"] + [b.keep_name for b in bores(cfg)]
    absent = [n for n in wanted if n not in ids]
    if absent:
        print(f"parts not found in the Part Studio: {absent}; found {sorted(ids)}", file=sys.stderr)
        return 1

    out = a.out
    (out / "keep_parts").mkdir(parents=True, exist_ok=True)
    print("exporting SPACE STEP ...")
    export_step(api, oc, ids["SPACE"], out / "space.step")
    print("exporting SPACE STL ...")
    export_stl(api, oc, ids["SPACE"], out / "space.stl")
    for n in wanted[1:]:
        print(f"exporting {n} STL ...")
        export_stl(api, oc, ids[n], out / "keep_parts" / f"{n}.stl")

    (out / "export.json").write_text(json.dumps({
        "source": "onshape",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "document": {k: oc[k] for k in ("base_url", "api_version", "document_id", "workspace_id", "element_id")},
        "part_ids": {n: ids[n] for n in wanted},
        "stl_params": oc["stl_params"],
    }, indent=2), encoding="utf-8")
    print(f"done; run `python -m pipeline.geometry_check` next")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
