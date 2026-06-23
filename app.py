from flask import Flask, render_template, request, Response
from kubernetes import client, config
import yaml

app = Flask(__name__)

APP_NAME = "k8s-dashboard-lite"
VERSION = "v3.3.0"

# ----------------------------
# K8S CONFIG
# ----------------------------
try:
    config.load_incluster_config()
except:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps = client.AppsV1Api()


# ----------------------------
# HELPERS
# ----------------------------
def get_namespaces():
    return [ns.metadata.name for ns in v1.list_namespace().items]


def base_context():
    ns = request.args.get("ns", "default")
    return {
        "namespaces": get_namespaces(),
        "selected_ns": ns,
        "app_name": APP_NAME,
        "version": VERSION
    }


def safe(obj):
    return yaml.safe_dump(obj.to_dict(), default_flow_style=False)


# ----------------------------
# DASHBOARD
# ----------------------------
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


# ----------------------------
# PODS
# ----------------------------
@app.route("/pods")
def pods():
    ns = request.args.get("ns", "default")
    search = request.args.get("search", "")

    items = v1.list_namespaced_pod(ns).items

    if search:
        items = [p for p in items if search.lower() in p.metadata.name.lower()]

    return render_template(
        "pods.html",
        pods=items,
        search=search,
        **base_context()
    )


# ----------------------------
# DEPLOYMENTS
# ----------------------------
@app.route("/deployments")
def deployments():
    ns = request.args.get("ns", "default")

    items = apps.list_namespaced_deployment(ns).items

    return render_template(
        "deployments.html",
        deployments=items,
        **base_context()
    )


# ----------------------------
# SERVICES
# ----------------------------
@app.route("/services")
def services():
    ns = request.args.get("ns", "default")

    items = v1.list_namespaced_service(ns).items

    return render_template(
        "services.html",
        services=items,
        **base_context()
    )


# ----------------------------
# YAML VIEW
# ----------------------------
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


# ----------------------------
# LOGS
# ----------------------------
@app.route("/logs/<ns>/<pod>")
def logs(ns, pod):

    def stream():
        try:
            for line in v1.read_namespaced_pod_log(
                name=pod,
                namespace=ns,
                follow=True,
                _preload_content=False
            ).stream():
                yield line.decode("utf-8")
        except Exception as e:
            yield f"\nERROR: {str(e)}\n"

    return Response(stream(), mimetype="text/plain")


# ----------------------------
# HEALTH
# ----------------------------
@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
