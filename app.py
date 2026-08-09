from flask import Flask, render_template, request, Response
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from datetime import datetime, timezone
import yaml

app = Flask(__name__)

APP_NAME = "k8s-dashboard-lite"
VERSION = "v5.0.0"

try:
    config.load_incluster_config()
except Exception:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps = client.AppsV1Api()


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


def pod_summary(p):
    cs = p.status.container_statuses or []
    ready = sum(1 for c in cs if c.ready)
    total = len(cs) if cs else len(p.spec.containers or [])
    restarts = sum(c.restart_count for c in cs) if cs else 0
    return {
        "name": p.metadata.name,
        "phase": p.status.phase or "Unknown",
        "ready": f"{ready}/{total}",
        "restarts": restarts,
        "node": p.spec.node_name or "-",
        "ip": p.status.pod_ip or "-",
        "age": age_of(p.metadata.creation_timestamp),
    }


def with_error(fn, default):
    try:
        return fn(), None
    except ApiException as e:
        return default, f"{e.status} {e.reason}"
    except Exception as e:  # noqa: BLE001
        return default, str(e)


# ---------- routes ----------
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

    return render_template(
        "index.html",
        pods=len(pods), deployments=len(deps), services=len(svcs),
        running=running, pending=pending, failed=failed, restarts=total_restarts,
        error=e1 or e2 or e3, **base_context(),
    )


@app.route("/pods")
def pods():
    ns = request.args.get("ns", "default")
    search = request.args.get("search", "")
    items, err = with_error(lambda: v1.list_namespaced_pod(ns).items, [])
    if search:
        items = [p for p in items if search.lower() in p.metadata.name.lower()]
    rows = [pod_summary(p) for p in items]
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


@app.route("/yaml/<kind>/<ns>/<name>")
def yaml_view(kind, ns, name):
    try:
        if kind == "pod":
            obj = v1.read_namespaced_pod(name, ns)
        elif kind == "deployment":
            obj = apps.read_namespaced_deployment(name, ns)
        elif kind == "service":
            obj = v1.read_namespaced_service(name, ns)
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


@app.route("/health")
def health():
    return {"status": "ok", "version": VERSION}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
