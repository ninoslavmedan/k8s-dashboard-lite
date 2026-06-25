from flask import Flask, render_template, request, Response
from kubernetes import client, config
import yaml

app = Flask(__name__)

APP_NAME = "k8s-dashboard-lite"
VERSION = "v4.0.0"

try:
    config.load_incluster_config()
except Exception:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps = client.AppsV1Api()

def get_namespaces():
    return [ns.metadata.name for ns in v1.list_namespace().items]

def base_context():
    return {
        "app_name": APP_NAME,
        "version": VERSION,
        "namespaces": get_namespaces(),
        "selected_ns": request.args.get("ns", "default")
    }

def safe(obj):
    return yaml.safe_dump(obj.to_dict(), default_flow_style=False)

@app.route("/")
def index():
    ns = request.args.get("ns", "default")
    return render_template(
        "index.html",
        pods=len(v1.list_namespaced_pod(ns).items),
        deployments=len(apps.list_namespaced_deployment(ns).items),
        services=len(v1.list_namespaced_service(ns).items),
        **base_context()
    )

@app.route("/pods")
def pods():
    ns = request.args.get("ns", "default")
    search = request.args.get("search", "")
    items = v1.list_namespaced_pod(ns).items
    if search:
        items = [p for p in items if search.lower() in p.metadata.name.lower()]
    return render_template("pods.html", pods=items, search=search, **base_context())

@app.route("/deployments")
def deployments():
    ns = request.args.get("ns", "default")
    return render_template(
        "deployments.html",
        deployments=apps.list_namespaced_deployment(ns).items,
        **base_context()
    )

@app.route("/services")
def services():
    ns = request.args.get("ns", "default")
    return render_template(
        "services.html",
        services=v1.list_namespaced_service(ns).items,
        **base_context()
    )

@app.route("/yaml/<kind>/<ns>/<name>")
def yaml_view(kind, ns, name):
    if kind == "pod":
        obj = v1.read_namespaced_pod(name, ns)
    elif kind == "deployment":
        obj = apps.read_namespaced_deployment(name, ns)
    elif kind == "service":
        obj = v1.read_namespaced_service(name, ns)
    else:
        return "Unsupported kind", 400
    return Response(safe(obj), mimetype="text/plain")

@app.route("/logs/<ns>/<pod>")
def logs(ns, pod):
    def stream():
        try:
            for line in v1.read_namespaced_pod_log(
                name=pod, namespace=ns, follow=True, _preload_content=False
            ).stream():
                yield line.decode("utf-8")
        except Exception as e:
            yield f"ERROR: {e}"
    return Response(stream(), mimetype="text/plain")

@app.route("/health")
def health():
    return {"status": "ok", "version": VERSION}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

