from flask import Flask, render_template, request, Response, jsonify
import yaml

from config import APP_NAME, APP_VERSION
from k8s_client import K8sClient

app = Flask(__name__)
k8s = K8sClient()


def to_yaml(obj):
    return yaml.safe_dump(obj.to_dict(), default_flow_style=False)


@app.route("/")
def index():
    ns = request.args.get("ns", "default")

    return render_template(
        "index.html",
        app_name=APP_NAME,
        version=APP_VERSION,
        namespaces=k8s.namespaces(),
        selected_ns=ns,
        pods=len(k8s.pods(ns)),
        deployments=len(k8s.deployments(ns)),
        services=len(k8s.services(ns)),
    )


@app.route("/pods")
def pods():
    ns = request.args.get("ns", "default")
    search = request.args.get("search", "")

    items = k8s.pods(ns)

    if search:
        items = [p for p in items if search.lower() in p.metadata.name.lower()]

    return render_template("pods.html", pods=items, ns=ns)


@app.route("/deployments")
def deployments():
    ns = request.args.get("ns", "default")
    return render_template("deployments.html", deployments=k8s.deployments(ns), ns=ns)


@app.route("/services")
def services():
    ns = request.args.get("ns", "default")
    return render_template("services.html", services=k8s.services(ns), ns=ns)


@app.route("/yaml/<kind>/<ns>/<name>")
def yaml_view(kind, ns, name):

    if kind == "pod":
        obj = k8s.v1.read_namespaced_pod(name, ns)
    elif kind == "deployment":
        obj = k8s.apps.read_namespaced_deployment(name, ns)
    elif kind == "service":
        obj = k8s.v1.read_namespaced_service(name, ns)
    else:
        return "Unsupported kind", 400

    return Response(to_yaml(obj), mimetype="text/plain")


@app.route("/logs/<ns>/<pod>")
def logs(ns, pod):

    def stream():
        try:
            for line in k8s.pod_logs(ns, pod):
                yield line
        except Exception as e:
            yield f"\n[ERROR] {str(e)}\n"

    return Response(stream(), mimetype="text/plain")


@app.route("/api/summary")
def summary():
    ns = request.args.get("ns", "default")

    return jsonify({
        "namespace": ns,
        "pods": len(k8s.pods(ns)),
        "deployments": len(k8s.deployments(ns)),
        "services": len(k8s.services(ns)),
        "version": APP_VERSION
    })


@app.route("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
