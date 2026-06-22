from flask import Flask, render_template
from kubernetes import client, config

app = Flask(__name__)

try:
    config.load_incluster_config()
except Exception:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()


@app.route("/")
def index():
    namespaces = len(v1.list_namespace().items)
    nodes = len(v1.list_node().items)
    pods = len(v1.list_pod_for_all_namespaces().items)
    deployments = len(apps_v1.list_deployment_for_all_namespaces().items)

    return render_template(
        "index.html",
        namespaces=namespaces,
        nodes=nodes,
        pods=pods,
        deployments=deployments,
    )


@app.route("/pods")
def pods():
    pods = v1.list_pod_for_all_namespaces().items
    return render_template("pods.html", pods=pods)


@app.route("/deployments")
def deployments():
    deployments = apps_v1.list_deployment_for_all_namespaces().items
    return render_template(
        "deployments.html",
        deployments=deployments,
    )


@app.route("/services")
def services():
    services = v1.list_service_for_all_namespaces().items
    return render_template(
        "services.html",
        services=services,
    )


@app.route("/nodes")
def nodes():
    nodes = v1.list_node().items
    return render_template(
        "nodes.html",
        nodes=nodes,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
