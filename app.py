from flask import Flask, render_template, request, Response
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from datetime import datetime, timezone
import yaml
import shlex
import subprocess

app = Flask(__name__)

APP_NAME = "k8s-dashboard-lite"
VERSION = "v7.0.0"

try:
    config.load_incluster_config()
except Exception:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps = client.AppsV1Api()
custom = client.CustomObjectsApi()  # for metrics.k8s.io


# ---------- helpers ----------
def get_namespaces():
    try:
        return [ns.metadata.name for ns in v1.list_namespace().items]
    except ApiException:
        return ["default"]


def base_context():
    return {
        "app_name": APP_NAME,
        "version": VERSION,
        "namespaces": get_namespaces(),
        "selected_ns": request.args.get("ns", "default"),
    }


def safe(obj):
    return yaml.safe_dump(obj.to_dict(), default_flow_style=False)


def age_of(ts):
    if not ts:
        return "-"
    s = int((datetime.now(timezone.utc) - ts).total_seconds())
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def with_error(fn, default):
    try:
        return fn(), None
    except ApiException as e:
        return default, f"{e.status} {e.reason}"
    except Exception as e:  # noqa: BLE001
        return default, str(e)


# ----- metrics parsing (metrics-server returns cpu in n/u/m cores, mem in Ki/Mi/Gi) -----
def cpu_to_millicores(v):
    """Normalize a metrics-server cpu string to integer millicores."""
    if v is None:
        return 0
    v = str(v)
    try:
        if v.endswith("n"):      # nanocores
            return int(int(v[:-1]) / 1_000_000)
        if v.endswith("u"):      # microcores
            return int(int(v[:-1]) / 1_000)
        if v.endswith("m"):      # millicores
            return int(v[:-1])
        return int(float(v) * 1000)  # whole cores
    except ValueError:
        return 0


def mem_to_mib(v):
    """Normalize a metrics-server / capacity memory string to integer MiB."""
    if v is None:
        return 0
    v = str(v)
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024,
             "K": 1000 / (1024 * 1024), "M": 1000 * 1000 / (1024 * 1024),
             "G": 1000 ** 3 / (1024 * 1024)}
    for u, factor in units.items():
        if v.endswith(u):
            try:
                return int(float(v[: -len(u)]) * factor)
            except ValueError:
                return 0
    try:
        return int(int(v) / (1024 * 1024))  # bytes
    except ValueError:
        return 0


def fmt_mem(mib):
    if mib >= 1024:
        return f"{mib / 1024:.1f} Gi"
    return f"{mib} Mi"


def fmt_cpu(m):
    if m >= 1000:
        return f"{m / 1000:.2f} cores"
    return f"{m}m"


# ---------- existing routes ----------
@app.route("/")
def index():
    ns = request.args.get("ns", "default")
    pods, e1 = with_error(lambda: v1.list_namespaced_pod(ns).items, [])
    deps, e2 = with_error(lambda: apps.list_namespaced_deployment(ns).items, [])
    svcs, e3 = with_error(lambda: v1.list_namespaced_service(ns).items, [])

    running = sum(1 for p in pods if p.status.phase == "Running")
    pending = sum(1 for p in pods if p.status.phase == "Pending")
    failed = max(len(pods) - running - pending, 0)
    total_restarts = sum(
        sum(c.restart_count for c in (p.status.container_statuses or [])) for p in pods
    )
    nodes, _ = with_error(lambda: v1.list_node().items, [])

    return render_template(
        "index.html",
        pods=len(pods), deployments=len(deps), services=len(svcs),
        running=running, pending=pending, failed=failed, restarts=total_restarts,
        nodes=len(nodes), error=e1 or e2 or e3, **base_context(),
    )


@app.route("/pods")
def pods():
    ns = request.args.get("ns", "default")
    search = request.args.get("search", "")
    items, err = with_error(lambda: v1.list_namespaced_pod(ns).items, [])
    if search:
        items = [p for p in items if search.lower() in p.metadata.name.lower()]
    rows = []
    for p in items:
        cs = p.status.container_statuses or []
        ready = sum(1 for c in cs if c.ready)
        total = len(cs) if cs else len(p.spec.containers or [])
        rows.append({
            "name": p.metadata.name,
            "phase": p.status.phase or "Unknown",
            "ready": f"{ready}/{total}",
            "restarts": sum(c.restart_count for c in cs) if cs else 0,
            "node": p.spec.node_name or "-",
            "ip": p.status.pod_ip or "-",
            "age": age_of(p.metadata.creation_timestamp),
        })
    return render_template("pods.html", pods=rows, search=search, error=err, **base_context())


@app.route("/deployments")
def deployments():
    ns = request.args.get("ns", "default")
    items, err = with_error(lambda: apps.list_namespaced_deployment(ns).items, [])
    rows = []
    for d in items:
        st = d.status
        rows.append({
            "name": d.metadata.name,
            "desired": d.spec.replicas or 0,
            "ready": st.ready_replicas or 0,
            "available": st.available_replicas or 0,
            "updated": st.updated_replicas or 0,
            "healthy": (st.ready_replicas or 0) == (d.spec.replicas or 0),
            "images": [c.image for c in d.spec.template.spec.containers],
            "age": age_of(d.metadata.creation_timestamp),
        })
    return render_template("deployments.html", deployments=rows, error=err, **base_context())


@app.route("/services")
def services():
    ns = request.args.get("ns", "default")
    items, err = with_error(lambda: v1.list_namespaced_service(ns).items, [])
    rows = []
    for s in items:
        ports = [
            f"{p.port}{'/' + p.protocol if p.protocol else ''}"
            + (f"->{p.target_port}" if p.target_port is not None else "")
            for p in (s.spec.ports or [])
        ]
        selector = ", ".join(f"{k}={v}" for k, v in (s.spec.selector or {}).items())
        rows.append({
            "name": s.metadata.name,
            "type": s.spec.type,
            "cluster_ip": s.spec.cluster_ip or "-",
            "ports": ", ".join(ports) or "-",
            "selector": selector or "-",
            "age": age_of(s.metadata.creation_timestamp),
        })
    return render_template("services.html", services=rows, error=err, **base_context())


# ---------- NEW: Nodes ----------
@app.route("/nodes")
def nodes():
    items, err = with_error(lambda: v1.list_node().items, [])

    # node usage from metrics-server (graceful fallback if unavailable)
    usage = {}
    try:
        m = custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes")
        for it in m.get("items", []):
            usage[it["metadata"]["name"]] = {
                "cpu_m": cpu_to_millicores(it["usage"]["cpu"]),
                "mem_mib": mem_to_mib(it["usage"]["memory"]),
            }
    except Exception:  # noqa: BLE001
        pass

    rows = []
    for n in items:
        name = n.metadata.name
        conds = {c.type: c.status for c in (n.status.conditions or [])}
        ready = conds.get("Ready") == "True"
        roles = [k.split("/")[1] for k in (n.metadata.labels or {})
                 if k.startswith("node-role.kubernetes.io/")] or ["worker"]
        cap = n.status.capacity or {}
        cpu_cap_m = cpu_to_millicores(cap.get("cpu", "0")) if not str(cap.get("cpu", "")).isdigit() else int(cap.get("cpu", 0)) * 1000
        mem_cap_mib = mem_to_mib(cap.get("memory", "0"))
        u = usage.get(name, {})
        cpu_u = u.get("cpu_m", 0)
        mem_u = u.get("mem_mib", 0)
        rows.append({
            "name": name,
            "ready": ready,
            "roles": ", ".join(roles),
            "version": n.status.node_info.kubelet_version if n.status.node_info else "-",
            "os": (n.status.node_info.os_image if n.status.node_info else "-"),
            "internal_ip": next((a.address for a in (n.status.addresses or [])
                                 if a.type == "InternalIP"), "-"),
            "cpu_usage": fmt_cpu(cpu_u) if cpu_u else "-",
            "cpu_pct": int(cpu_u / cpu_cap_m * 100) if cpu_cap_m else 0,
            "mem_usage": fmt_mem(mem_u) if mem_u else "-",
            "mem_pct": int(mem_u / mem_cap_mib * 100) if mem_cap_mib else 0,
            "age": age_of(n.metadata.creation_timestamp),
        })
    return render_template("nodes.html", nodes=rows, error=err, **base_context())


# ---------- NEW: Metrics (pod CPU/mem) ----------
@app.route("/metrics-view")
def metrics_view():
    ns = request.args.get("ns", "default")
    rows = []
    err = None
    try:
        m = custom.list_namespaced_custom_object(
            "metrics.k8s.io", "v1beta1", ns, "pods"
        )
        for it in m.get("items", []):
            cpu = sum(cpu_to_millicores(c["usage"]["cpu"]) for c in it.get("containers", []))
            mem = sum(mem_to_mib(c["usage"]["memory"]) for c in it.get("containers", []))
            rows.append({
                "name": it["metadata"]["name"],
                "cpu_m": cpu,
                "cpu": fmt_cpu(cpu),
                "mem_mib": mem,
                "mem": fmt_mem(mem),
                "containers": len(it.get("containers", [])),
            })
        rows.sort(key=lambda r: r["cpu_m"], reverse=True)
    except ApiException as e:
        err = f"{e.status} {e.reason} (metrics-server available?)"
    except Exception as e:  # noqa: BLE001
        err = f"{e} (metrics-server available?)"

    max_cpu = max((r["cpu_m"] for r in rows), default=1) or 1
    max_mem = max((r["mem_mib"] for r in rows), default=1) or 1
    for r in rows:
        r["cpu_bar"] = int(r["cpu_m"] / max_cpu * 100)
        r["mem_bar"] = int(r["mem_mib"] / max_mem * 100)
    return render_template("metrics.html", metrics=rows, error=err, **base_context())


# ---------- NEW: Events ----------
@app.route("/events")
def events():
    ns = request.args.get("ns", "default")
    items, err = with_error(
        lambda: v1.list_namespaced_event(ns).items, []
    )
    # newest first
    def keyfn(e):
        return e.last_timestamp or e.event_time or e.metadata.creation_timestamp
    try:
        items = sorted(items, key=keyfn, reverse=True)
    except Exception:  # noqa: BLE001
        pass
    rows = []
    for e in items[:200]:
        rows.append({
            "type": e.type or "Normal",
            "reason": e.reason or "-",
            "object": f"{e.involved_object.kind}/{e.involved_object.name}"
                      if e.involved_object else "-",
            "message": (e.message or "").strip(),
            "count": e.count or 1,
            "age": age_of(e.last_timestamp or e.metadata.creation_timestamp),
        })
    return render_template("events.html", events=rows, error=err, **base_context())


@app.route("/yaml/<kind>/<ns>/<name>")
def yaml_view(kind, ns, name):
    try:
        if kind == "pod":
            obj = v1.read_namespaced_pod(name, ns)
        elif kind == "deployment":
            obj = apps.read_namespaced_deployment(name, ns)
        elif kind == "service":
            obj = v1.read_namespaced_service(name, ns)
        elif kind == "node":
            obj = v1.read_node(name)
        else:
            return "Unsupported kind", 400
    except ApiException as e:
        return f"Error: {e.status} {e.reason}", e.status or 500
    return Response(safe(obj), mimetype="text/plain")


@app.route("/logs/<ns>/<pod>")
def logs(ns, pod):
    def stream():
        try:
            for line in v1.read_namespaced_pod_log(
                name=pod, namespace=ns, follow=True, _preload_content=False
            ).stream():
                yield line.decode("utf-8")
        except Exception as e:  # noqa: BLE001
            yield f"ERROR: {e}"
    return Response(stream(), mimetype="text/plain")



# ---------- NEW: kubectl terminal (command runner) ----------
# Safety: only `kubectl` is allowed, arguments are shell-split (no shell=True),
# and a set of destructive verbs is blocked by default.
BLOCKED_VERBS = {
    "delete", "drain", "cordon", "uncordon", "taint", "edit",
    "replace", "apply", "patch", "scale", "annotate", "label",
    "create", "exec", "attach", "cp", "run", "expose", "set",
    "rollout",  # rollout can restart/undo; block by default
}
KUBECTL_TIMEOUT = 20  # seconds


@app.route("/terminal")
def terminal():
    return render_template("terminal.html", no_refresh=True, **base_context())


@app.route("/run", methods=["POST"])
def run_kubectl():
    raw = (request.form.get("cmd") or request.json.get("cmd") if request.is_json
           else request.form.get("cmd") or "").strip()
    if not raw:
        return Response("(empty command)", mimetype="text/plain")

    # tokenize safely (no shell interpretation)
    try:
        parts = shlex.split(raw)
    except ValueError as e:
        return Response(f"parse error: {e}", mimetype="text/plain", status=400)

    # strip a leading "kubectl" if the user typed it
    if parts and parts[0] == "kubectl":
        parts = parts[1:]
    if not parts:
        return Response("(no kubectl subcommand)", mimetype="text/plain")

    verb = parts[0].lower()
    if verb in BLOCKED_VERBS:
        return Response(
            f"blocked: '{verb}' is disabled in this read-only terminal.\n"
            f"Allowed: get, describe, logs, top, explain, api-resources, version, config, cluster-info, etc.",
            mimetype="text/plain", status=403,
        )

    cmd = ["kubectl"] + parts
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=KUBECTL_TIMEOUT
        )
        body = out.stdout
        if out.stderr:
            body += ("\n" if body else "") + out.stderr
        if not body:
            body = f"(exit {out.returncode}, no output)"
        return Response(body, mimetype="text/plain", status=200)
    except subprocess.TimeoutExpired:
        return Response(f"timeout after {KUBECTL_TIMEOUT}s", mimetype="text/plain", status=504)
    except FileNotFoundError:
        return Response("kubectl not found in container", mimetype="text/plain", status=500)
    except Exception as e:  # noqa: BLE001
        return Response(f"error: {e}", mimetype="text/plain", status=500)


@app.route("/health")
def health():
    return {"status": "ok", "version": VERSION}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
