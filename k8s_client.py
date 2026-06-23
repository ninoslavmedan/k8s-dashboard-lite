from kubernetes import client, config

class K8sClient:
    def __init__(self):
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()

        self.v1 = client.CoreV1Api()
        self.apps = client.AppsV1Api()

    def namespaces(self):
        return [n.metadata.name for n in self.v1.list_namespace().items]

    def pods(self, ns):
        return self.v1.list_namespaced_pod(ns).items

    def deployments(self, ns):
        return self.apps.list_namespaced_deployment(ns).items

    def services(self, ns):
        return self.v1.list_namespaced_service(ns).items

    def pod_logs(self, ns, pod):
        return self.v1.read_namespaced_pod_log(
            name=pod,
            namespace=ns,
            follow=True,
            tail_lines=200
        )
