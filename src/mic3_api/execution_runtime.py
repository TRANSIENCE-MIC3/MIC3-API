"""Optional execution composition; importing the API never loads these clients."""
from concurrent.futures import ThreadPoolExecutor

from mic3_api.application.runs.artifacts import VerifyOutputs
from mic3_api.application.runs.execution import ReconcileExecutions
from mic3_api.execution_loop import run_execution
from mic3_api.infrastructure.job_renderer import HelmJobRenderer
from mic3_api.infrastructure.kubernetes_jobs import KubernetesJobs
from mic3_api.infrastructure.persistence.run_execution import SqlAlchemyExecutions
from mic3_api.infrastructure.s3_artifacts import S3ArtifactInventory


def run_with_execution(database, consumer_factory, settings, stop, accept_loop):
    jobs = inventory = None
    try:
        renderer = HelmJobRenderer(settings.execution_chart, settings.execution_values_file)
        jobs = KubernetesJobs.connect(settings.execution_kubeconfig, settings.execution_context,
                                      renderer, settings.execution_namespace)
        inventory = S3ArtifactInventory.connect(settings)
        coordinator = ReconcileExecutions(SqlAlchemyExecutions(database.engine), jobs,
            VerifyOutputs(inventory), settings.execution_namespace, settings.execution_capacity)
        def supervised(function, *args):
            try:
                return function(*args)
            finally:
                stop.set()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='mic3-worker') as pool:
            tasks = [pool.submit(supervised, accept_loop, database, consumer_factory, settings, stop),
                     pool.submit(supervised, run_execution, coordinator, stop, settings.execution_poll_seconds)]
            return max(task.result() for task in tasks)
    finally:
        if inventory:
            inventory.close()
        if jobs:
            jobs.close()
