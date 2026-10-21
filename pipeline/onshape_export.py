"""Export SPACE (STEP + fine STL) and every KEEP_* part (fine STL) of one part's Part Studio.

    python -m pipeline.onshape_export --part <part> [--out geometry/<part>]

The part's Part Studio is config/parts/<part>.yaml `onshape.url` (the Part Studio URL) or `onshape.element_id` (plus
`document_id` / `workspace_id` for another document); the defaults for those, the host and the STL
parameters come from config/onshape.yaml. Keeps are found by name (KEEP_<ROLE>_<label>[_<k>]), not from the
config. Needs ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY in the environment (or the Windows user environment).
Writes space.step, space.stl, keep_parts/KEEP_*.stl, regions.json and export.json.
Endpoints verified against skildai.onshape.com on 2026-10-15 (STL tolerance units: see onshape.yaml); the
FeatureScript evaluate and mass-properties endpoints on 2026-10-20.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

from pipeline.config import REPO, geometry_dir, load_part
from pipeline.regions import build_regions, save_regions

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
        # Exports redirect to a regional download host (e.g. skildai -> cad-usw2.onshape.com);
        # send credentials to Onshape hosts only.
        for _ in range(5):
            if r.status_code not in (301, 302, 303, 307, 308):
                break
            loc = r.headers["Location"]
            host = urlparse(loc).netloc
            trusted = host in ("", urlparse(self.base).netloc) or host.endswith(".onshape.com")
            r = (self.s if trusted else requests).get(loc, allow_redirects=False, timeout=300,
                                                       headers=kw.get("headers"))
        if not r.ok:
            raise RuntimeError(f"{method} {url} -> HTTP {r.status_code}: {r.text[:500]}")
        return r

    def get(self, path: str, params=None, accept="application/json") -> requests.Response:
        return self._send("GET", self.url(path), params=params, headers={"Accept": accept})

    def post(self, path: str, body: dict) -> requests.Response:
        return self._send("POST", self.url(path), json=body,
                          headers={"Accept": "application/json", "Content-Type": "application/json"})


def parse_url(url: str) -> dict:
    """base_url, document_id, workspace_id and element_id from a Part Studio URL
    (https://<host>/documents/<did>/w/<wid>/e/<eid>)."""
    u = urlparse(url)
    m = re.match(r"^/documents/([0-9a-f]{24})/w/([0-9a-f]{24})/e/([0-9a-f]{24})", u.path)
    if not (u.scheme and u.netloc and m):
        raise ValueError(f"not an Onshape Part Studio workspace URL: {url!r} "
                         "(expected https://<host>/documents/<did>/w/<wid>/e/<eid>)")
    return {"base_url": f"{u.scheme}://{u.netloc}", "document_id": m.group(1), "workspace_id": m.group(2),
            "element_id": m.group(3)}


def _dwe(oc: dict) -> str:
    return f"d/{oc['document_id']}/w/{oc['workspace_id']}/e/{oc['element_id']}"


def api_key(name: str) -> str:
    """From the process environment, else (Windows) the user environment, which a shell started before the
    keys were set doesn't see."""
    v = os.environ.get(name)
    if v:
        return v
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            pass
    raise KeyError(name)


def _fs_value(v):
    """Decode a FeatureScript evaluation result (BTFSValue*) into plain Python."""
    t = v.get("btType", "")
    if t.endswith("BTFSValueArray"):
        return [_fs_value(x) for x in v["value"]]
    if t.endswith("BTFSValueMap"):
        return {_fs_value(e["key"]): _fs_value(e["value"]) for e in v["value"]}
    return v.get("value")


KEEP_ATTRIBUTES_FS = """function(context is Context, queries is map) {
  var out = [];
  for (var b in evaluateQuery(context, qBodyType(qEverything(EntityType.BODY), BodyType.SOLID)))
  {
    for (var a in getAttributes(context, { "entities" : b }))
      if (a is map && a.kind == "PreserveRegion")
        out = append(out, a);
  }
  return out;
}"""


def keep_attributes(api: Onshape, oc: dict) -> dict[str, dict]:
    """PreserveRegion attributes of the feature-made keeps, keyed by the keep name the feature gave them."""
    r = api.post(f"partstudios/{_dwe(oc)}/featurescript", {"script": KEEP_ATTRIBUTES_FS, "queries": {}}).json()
    if r.get("notices"):
        raise RuntimeError(f"attribute evaluation reported: {[n.get('message') for n in r['notices']]}")
    out = {}
    for a in _fs_value(r["result"]):
        if a["name"] in out:
            raise RuntimeError(f"two keeps carry the attribute name {a['name']!r}")
        out[a["name"]] = a
    return out


def onshape_volume_mm3(api: Onshape, oc: dict, part_id: str) -> float:
    """Exact (B-rep) volume from Onshape's mass properties; the body comes back keyed "-all-" in m^3."""
    bodies = api.get(f"partstudios/{_dwe(oc)}/massproperties", params={"partId": part_id}).json()["bodies"]
    (body,) = bodies.values()
    return float(body["volume"][0]) * 1e9


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
    if not r.content:
        # Onshape answers 200 with an empty body when it cannot honour the tolerances (e.g. chord too fine).
        raise RuntimeError(f"STL export of part {part_id} returned an empty file; check stl_params in onshape.yaml")
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
    ap.add_argument("--part", required=True, help="config/parts/<part>.yaml")
    ap.add_argument("--out", type=Path, help="default geometry/<part>")
    a = ap.parse_args(argv)

    part = load_part(a.part)
    oc = yaml.safe_load(ONSHAPE_CONFIG.read_text(encoding="utf-8"))
    oc.update(part.get("onshape") or {})
    if oc.get("url"):
        oc.update(parse_url(oc["url"]))
    missing = [k for k in ("document_id", "workspace_id", "element_id") if not oc.get(k)]
    if missing:
        print(f"Onshape IDs missing (config/onshape.yaml or the part's `onshape`): {', '.join(missing)}",
              file=sys.stderr)
        return 2
    try:
        api = Onshape(oc["base_url"], oc["api_version"], api_key("ONSHAPE_ACCESS_KEY"),
                      api_key("ONSHAPE_SECRET_KEY"))
    except KeyError as e:
        print(f"environment variable {e} is not set", file=sys.stderr)
        return 2

    ids = part_ids_by_name(api, oc)
    if "SPACE" not in ids:
        print(f"no part named SPACE in the Part Studio; found {sorted(ids)}", file=sys.stderr)
        return 1
    keeps = {n: pid for n, pid in ids.items() if n.startswith("KEEP_")}
    ignored = sorted(set(ids) - set(keeps) - {"SPACE"})
    if ignored:
        print(f"ignoring parts that are neither SPACE nor KEEP_*: {ignored}")
    attrs = keep_attributes(api, oc)
    stray = sorted(set(attrs) - set(keeps))
    if stray:
        print(f"Preserve region attributes on parts that aren't named as keeps (renamed?): {stray}", file=sys.stderr)
        return 1
    try:
        regions = build_regions(keeps, attrs, (part.get("keeps") or {}).get("wall_thickness"))
    except ValueError as e:
        print(f"keep regions: {e}", file=sys.stderr)
        return 1

    out = a.out or geometry_dir(a.part)
    kdir = out / "keep_parts"
    kdir.mkdir(parents=True, exist_ok=True)
    for old in kdir.glob("KEEP_*.stl"):      # a keep deleted in Onshape must not survive from a previous export
        old.unlink()
    print("exporting SPACE STEP ...")
    export_step(api, oc, ids["SPACE"], out / "space.step")
    print("exporting SPACE STL ...")
    export_stl(api, oc, ids["SPACE"], out / "space.stl")
    for n in sorted(keeps):
        print(f"exporting {n} STL ...")
        export_stl(api, oc, keeps[n], kdir / f"{n}.stl")
    volume = onshape_volume_mm3(api, oc, ids["SPACE"])

    doc = {k: oc[k] for k in ("base_url", "api_version", "document_id", "workspace_id", "element_id")}
    save_regions(out / "regions.json", regions, {"part": a.part, "document": doc})
    (out / "export.json").write_text(json.dumps({
        "source": "onshape",
        "part": a.part,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "document": doc,
        "part_ids": {"SPACE": ids["SPACE"], **keeps},
        "ignored_parts": ignored,
        "space_volume_mm3": volume,
        "stl_params": oc["stl_params"],
    }, indent=2), encoding="utf-8")
    for r in regions:
        print(f"  region {r.name:12s} {r.source:9s} t={r.wall_thickness} keeps={[k.part for k in r.keeps]}")
    print(f"done; run `python -m pipeline.geometry_check --part {a.part}` next")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
